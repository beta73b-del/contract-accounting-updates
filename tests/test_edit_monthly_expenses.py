"""Регрессия изменения/удаления прочего расхода без миграции или дублей."""
import os
import sys
import tempfile
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'app'))
import db


def test_edit_and_delete_update_summary_without_touching_other_month():
    with tempfile.TemporaryDirectory() as folder:
        path = os.path.join(folder, 'test.db')
        conn = db.get_connection(path)
        first = db.insert_monthly_expense(conn, {'expense_date': '2026-10-01', 'amount': 1500})
        second = db.insert_monthly_expense(conn, {'expense_date': '2026-11-01', 'amount': 400})
        assert db.update_monthly_expense_amount(conn, first, 2300, 2026, 10)
        assert not db.update_monthly_expense_amount(conn, second, 1000, 2026, 10)
        assert len(list(db.fetch_monthly_expenses(conn, 2026, 10))) == 1
        assert db.monthly_summary(conn, 2026, 10)[0]['monthly_expenses'] == 2300
        assert db.monthly_summary(conn, 2026, 10)[0]['profit'] == -2300
        assert db.monthly_summary(conn, 2026, 11)[0]['monthly_expenses'] == 400
        conn.close()
        conn = db.get_connection(path)
        assert db.monthly_summary(conn, 2026, 10)[0]['monthly_expenses'] == 2300
        db.delete_monthly_expense(conn, first)
        assert not list(db.fetch_monthly_expenses(conn, 2026, 10))
        assert db.monthly_summary(conn, 2026, 11)[0]['monthly_expenses'] == 400
        conn.close()


def test_invalid_edit_does_not_change_database():
    with tempfile.TemporaryDirectory() as folder:
        conn = db.get_connection(os.path.join(folder, 'test.db'))
        expense_id = db.insert_monthly_expense(conn, {'expense_date': '2026-10-01', 'amount': 250})
        for value in (0, -5, float('nan'), float('inf')):
            try:
                db.update_monthly_expense_amount(conn, expense_id, value, 2026, 10)
            except ValueError:
                pass
            else:
                raise AssertionError(f'Accepted invalid amount {value}')
        assert float(list(db.fetch_monthly_expenses(conn, 2026, 10))[0]['amount']) == 250
        conn.close()


if __name__ == '__main__':
    test_edit_and_delete_update_summary_without_touching_other_month()
    test_invalid_edit_does_not_change_database()
    print('PASS: 2 monthly expense editing tests')
