---
name: Answering questions from a SQL database
description: Read the schema, compute the answer in one query, and check every condition the question sets (status, dates, exclusions).
---
1. List the tables and describe the ones the question touches before writing a query. Column names and
   relationships are rarely what you would guess.
2. Find where each number lives. An order's value may sit in an items table (quantity times unit price), not in a
   total column. A product's current price is not the price an old order was paid at.
3. Turn every condition in the question into SQL: status values (paid, cancelled, refunded), date ranges, and
   exclusions such as test accounts. Date ranges include both ends unless the question says otherwise. Check the
   last day of a period.
4. When the question says "after" something (refunds, discounts), join it in and subtract it per row before
   grouping.
5. Compute in SQL, not by hand. Group by an id, not a name: two rows can share a name.
6. Run one query to answer and, if the result is close or surprising, a second one that checks it another way.
7. Answer with exactly what was asked (a name, a number), rounded as asked.
