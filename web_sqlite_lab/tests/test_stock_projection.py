"""Synthetic event ledger invariants, independent of original Excel files."""
from copy import deepcopy
import unittest
from stock_projection import project,weighted_cost,StockError

def event(eid,kind,*,lines=None,needs=None,cid="",phase="",**extra):
    return {"id":eid,"kind":kind,"date":"2026-11-25","party":"","note":"",
            "contractId":cid,"phase":phase,"lines":lines or [],"needs":needs or [],**extra}

def line(q,c=0,product="Тест 1010"):
    return {"product":product,"quantity":q,"cents":c}

def need(q,p="Тест 1010"):
    return {"product":p,"quantity":q}

class ReplayTests(unittest.TestCase):
    def check_stock(self,events,expected,product="Тест 1010"):
        x=project(events,default_products=("Тест 1010","Тест 3120"))
        item=x["inventory"][product]
        self.assertEqual((item["quantity"],item["cents"],item["reserved"],item["available"],item["shortage"]),expected)
        return x
    def test_weighted_cost_round_half_up_and_full_issue(self):
        self.assertEqual(weighted_cost(2,1,1),1)
        self.assertEqual(weighted_cost(3,100,3),100)
        with self.assertRaises(StockError):weighted_cost(1,100,2)
    def test_receipt_then_shipment_recomputes_cost(self):
        e=[event("r1","receipt",lines=[line(10,10010)]),
           event("rsv","reserve",cid="a",phase="reserved",needs=[need(3)]),
           event("s1","ship",cid="a",phase="shipped",
                 needs=[need(3)],lines=[line(-3,-999)])]
        old=deepcopy(e)
        result=self.check_stock(e,(7,7007,0,7,0))
        self.assertEqual(result["events"][-1]["lines"][0]["cents"],-3003)
        self.assertEqual(e,old,"Source event log must never be modified")
    def test_reserved_shortage_may_be_negative(self):
        e=[event("r1","receipt",lines=[line(2,2000)]),
           event("rv","reserve",cid="a",phase="reserved",needs=[need(5)])]
        self.check_stock(e,(2,2000,5,-3,3))
    def test_release_reserve(self):
        e=[event("r1","receipt",lines=[line(2,2000)]),
           event("rv","reserve",cid="a",phase="reserved",needs=[need(5)]),
           event("rel","release",cid="a",phase="released")]
        self.check_stock(e,(2,2000,0,2,0))
    def test_correction_reapplies_to_original_receipt_and_reprices_shipment(self):
        e=[event("r1","receipt",lines=[line(10,10000)]),
           event("s1","ship",cid="a",phase="shipped",
                 needs=[need(3)],lines=[line(-3,-3000)]),
           event("fix","editReceipt",target="r1",correctedQuantity=5,
                 correctedCents=7500,correctedDate="2026-11-20",
                 party="Test Supplier",lines=[line(0,0)])]
        result=self.check_stock(e,(2,3000,0,2,0))
        self.assertEqual(result["events"][0]["lines"][0]["quantity"],5)
        self.assertEqual(result["events"][1]["lines"][0]["cents"],-4500)
        self.assertEqual(result["events"][2]["lines"][0]["cents"],0)
    def test_delete_receipt_can_fail_historical_stock(self):
        e=[event("r1","receipt",lines=[line(10,10000)]),
           event("s1","ship",cid="a",phase="shipped",needs=[need(3)],lines=[line(-3,-3000)]),
           event("del","deleteReceipt",target="r1",correctedQuantity=0,
                 correctedCents=0,lines=[line(0,0)])]
        with self.assertRaises(StockError):project(e)
    def test_delete_unused_receipt_without_ghost_movement(self):
        e=[event("r1","receipt",lines=[line(10,10000)]),
           event("del","deleteReceipt",target="r1",correctedQuantity=0,
                 correctedCents=0,lines=[line(0,0)])]
        result=self.check_stock(e,(0,0,0,0,0))
        self.assertTrue(result["events"][0]["deleted"])
    def test_adjust_target_rewrites_projection(self):
        e=[event("r1","receipt",lines=[line(10,10000)]),
           event("a1","adjust",targetQuantity=4,targetCents=4400,lines=[line(0,0)])]
        result=self.check_stock(e,(4,4400,0,4,0))
        self.assertEqual(result["events"][-1]["lines"][0]["quantity"],-6)
    def test_adjust_negative_uses_current_weighted_cost(self):
        e=[event("r1","receipt",lines=[line(10,10000)]),
           event("a1","adjust",lines=[line(-4,-1)])]
        result=self.check_stock(e,(6,6000,0,6,0))
        self.assertEqual(result["events"][-1]["lines"][0]["cents"],-4000)
    def test_return_reuses_original_shipment_cost(self):
        e=[event("r1","receipt",lines=[line(10,10000)]),
           event("s1","ship",cid="a",phase="shipped",needs=[need(3)],
                 lines=[line(-3,-3000)]),
           event("ret","return",cid="a",phase="returned",
                 needs=[need(3)],lines=[line(3,0)])]
        result=self.check_stock(e,(10,10000,0,10,0))
        self.assertTrue(result["contracts"]["a"]["returnedPending"])
        self.assertEqual(result["events"][-1]["lines"][0]["cents"],3000)
    def test_cancel_restores_shipment_and_re_reserves(self):
        e=[event("r1","receipt",lines=[line(10,10000)]),
           event("s1","ship",cid="a",phase="shipped",needs=[need(3)],
                 lines=[line(-3,0)]),
           event("cancel","cancel",cid="a",phase="reserved",
                 needs=[need(3)],lines=[line(3,0)])]
        self.check_stock(e,(10,10000,3,7,0))
    def test_invalid_return_is_rejected(self):
        with self.assertRaises(StockError):
            project([event("ret","return",cid="a",phase="returned",lines=[line(3,0)])])
    def test_duplicate_event_ids_are_rejected(self):
        with self.assertRaises(StockError):
            project([event("r1","receipt",lines=[line(2,100)]),
                     event("r1","receipt",lines=[line(2,100)])])
    def test_cannot_delete_product_with_remaining_reserved(self):
        e=[event("a1","reserve",cid="a",phase="reserved",needs=[need(3)]),
           event("d1","deleteProduct",lines=[line(0,0)])]
        with self.assertRaises(StockError):project(e)
    def test_multi_product_event_and_rollback_on_error(self):
        e=[event("r1","receipt",lines=[line(4,4000)]),
           event("r2","receipt",lines=[line(1,1800,"Тест 3120")]),
           event("s1","ship",cid="a",phase="shipped",needs=[need(2),need(2,"Тест 3120")],
                 lines=[line(-2,0),line(-2,0,"Тест 3120")])]
        before=deepcopy(e)
        with self.assertRaises(StockError):project(e,default_products=("Тест 1010","Тест 3120"))
        self.assertEqual(e,before)
if __name__=="__main__":unittest.main()
