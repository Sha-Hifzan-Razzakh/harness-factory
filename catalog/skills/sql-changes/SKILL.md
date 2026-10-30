---
name: Changing rows safely
description: Select before you update or delete, change only the rows the task names, mind the order of dependent deletes, and verify afterwards.
---
1. Write the WHERE clause as a SELECT first and look at the rows it returns. The count should match what the task
   implies.
2. Change only those rows. Never run an UPDATE or DELETE without a WHERE clause.
3. Rows that reference other rows: move or delete the referencing rows (items, child records) before the rows
   they point to. Delete a parent first and a later subquery may no longer find the children.
4. When a value must come from history (a price list, a log), take the entry in effect on the date the task
   gives. Entries dated later are not in effect yet, and the highest value is not always the latest.
5. Comparisons the task calls case-insensitive (emails, codes) need lower() on both sides.
6. After the change, query again to confirm the new state, and that rows you should not touch are unchanged.
