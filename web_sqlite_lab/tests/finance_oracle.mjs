// Differential QA driver. Imports current working Web's PURE logic.mjs only;
// sends fake records, no HTTP calls, Excel reads, or user data.
import {pathToFileURL} from 'node:url';
let src='';
for await (const part of process.stdin) src+=part;
const suites=JSON.parse(src);
if(!Array.isArray(suites))throw Error('Expected array of synthetic test cases');
if(!process.argv[2])throw Error('Pass actual absolute path to original app/logic.mjs');
const original=await import(pathToFileURL(process.argv[2]).href);
const results=suites.map(s=>original.calculateFinancialSummary(s.records,s.year,s.finance));
process.stdout.write(JSON.stringify(results));
