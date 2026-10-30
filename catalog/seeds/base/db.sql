CREATE TABLE customers (id INTEGER PRIMARY KEY, name TEXT NOT NULL, city TEXT NOT NULL);
INSERT INTO customers VALUES
  (1, 'Al Noor Trading', 'Dubai'),
  (2, 'Harbor Logistics', 'Abu Dhabi'),
  (3, 'Falcon Systems', 'Dubai'),
  (4, 'Oasis Foods', 'Sharjah'),
  (5, 'Creek Analytics', 'Dubai');

CREATE TABLE invoices (
  id INTEGER PRIMARY KEY,
  customer_id INTEGER NOT NULL REFERENCES customers(id),
  amount REAL NOT NULL,
  issued_on TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('paid', 'unpaid', 'overdue'))
);
INSERT INTO invoices VALUES
  (101, 1, 1200.00, '2025-11-03', 'unpaid'),
  (102, 2,  850.50, '2025-12-15', 'paid'),
  (103, 3, 4300.00, '2025-12-28', 'unpaid'),
  (104, 4,  610.00, '2026-01-09', 'unpaid'),
  (105, 5, 2250.00, '2026-02-01', 'paid');
