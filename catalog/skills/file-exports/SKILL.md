---
name: Writing CSV and report files
description: Match the requested header, columns, order and tie-breaks exactly, and check the rows against the source.
---
1. Use exactly the header and column order the task gives. No extra columns, no index column.
2. No quoting unless a value contains a comma or the task asks for it.
3. Sort as asked. When the task names a tie-break (for example name A to Z), apply it; values that tie on the
   first key are common.
4. Limits like "top 5" apply after sorting and tie-breaking.
5. Build the rows from a query or from the source file, not from memory. When comparing two sources, match on
   the key the task names and treat a missing key and a different value as two different reasons to report.
6. Keep rows in the order the task asks for: "in statement order" means the order of the input.
7. After writing, read the file back and count the rows against the query.
