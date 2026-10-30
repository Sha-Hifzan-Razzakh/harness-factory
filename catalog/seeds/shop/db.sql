-- Shop seed: customers, a product catalog with price history, orders, items and refunds.
CREATE TABLE customers (
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL,
  email TEXT NOT NULL,
  city TEXT NOT NULL,
  created_on TEXT NOT NULL
);
INSERT INTO customers VALUES
  (1, 'Al Noor Trading', 'accounts@alnoor.ae', 'Dubai', '2024-02-11'),
  (2, 'Harbor Logistics', 'finance@harborlog.ae', 'Abu Dhabi', '2024-05-03'),
  (3, 'Falcon Systems', 'billing@falconsys.ae', 'Dubai', '2025-03-02'),
  (4, 'Oasis Foods', 'ap@oasisfoods.ae', 'Sharjah', '2024-09-19'),
  (5, 'Creek Analytics', 'hello@creekanalytics.ae', 'Dubai', '2025-01-27'),
  (6, 'Desert Rose Interiors', 'orders@desertrose.ae', 'Ajman', '2025-06-08'),
  (7, 'Marina Fitness', 'admin@marinafit.ae', 'Dubai', '2025-07-21'),
  (8, 'Palm Pharmacy', 'purchasing@palmpharmacy.ae', 'Abu Dhabi', '2025-10-02'),
  (9, 'Gulf Print House', 'jobs@gulfprint.ae', 'Sharjah', '2026-01-15'),
  (10, 'Sandstone Cafe', 'owner@sandstonecafe.ae', 'Dubai', '2026-03-09'),
  (11, 'Falcon Systems', 'accounts@falconsys.ae', 'Dubai', '2026-08-14'),
  (12, 'QA Test Account', 'qa1@example.test', 'Dubai', '2026-02-01'),
  (13, 'Load Test Bot', 'load@example.test', 'Dubai', '2026-02-01');

CREATE TABLE products (
  id INTEGER PRIMARY KEY,
  sku TEXT NOT NULL UNIQUE,
  name TEXT NOT NULL,
  category TEXT NOT NULL,
  price REAL NOT NULL
);
INSERT INTO products VALUES
  (1, 'KB-100', 'Wireless Keyboard', 'accessories', 270.00),
  (2, 'MS-210', 'Ergonomic Mouse', 'accessories', 160.00),
  (3, 'HD-330', 'USB-C Hub', 'accessories', 280.00),
  (4, 'LT-900', 'Laptop Stand', 'accessories', 220.00),
  (5, 'MN-27', '27-inch Monitor', 'displays', 1050.00),
  (6, 'MN-32', '32-inch Monitor', 'displays', 1650.00),
  (7, 'DK-500', 'Standing Desk', 'furniture', 1900.00),
  (8, 'CH-750', 'Office Chair', 'furniture', 820.00),
  (9, 'SW-OFF', 'Office Suite License', 'software', 400.00),
  (10, 'SW-SEC', 'Security Suite License', 'software', 300.00);

CREATE TABLE price_history (
  product_id INTEGER NOT NULL REFERENCES products(id),
  price REAL NOT NULL,
  effective_on TEXT NOT NULL
);
INSERT INTO price_history VALUES
  (1, 120.00, '2025-01-01'),
  (1, 135.00, '2026-04-01'),
  (2, 80.00, '2025-01-01'),
  (2, 85.00, '2026-12-01'),
  (3, 150.00, '2025-01-01'),
  (3, 140.00, '2026-07-01'),
  (4, 95.00, '2025-06-01'),
  (4, 110.00, '2026-02-01'),
  (5, 1100.00, '2025-01-01'),
  (5, 1050.00, '2026-05-01'),
  (6, 1650.00, '2025-01-01'),
  (7, 1900.00, '2025-01-01'),
  (8, 780.00, '2025-01-01'),
  (8, 820.00, '2026-03-01'),
  (9, 400.00, '2025-01-01'),
  (10, 250.00, '2025-01-01'),
  (10, 300.00, '2026-09-01');

CREATE TABLE orders (
  id INTEGER PRIMARY KEY,
  customer_id INTEGER NOT NULL REFERENCES customers(id),
  ordered_on TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('pending', 'paid', 'cancelled', 'refunded'))
);
INSERT INTO orders VALUES
  (2001, 1, '2025-11-20', 'paid'),
  (2002, 2, '2025-12-05', 'paid'),
  (2003, 3, '2026-01-12', 'paid'),
  (2004, 4, '2026-01-25', 'paid'),
  (2005, 5, '2026-02-03', 'cancelled'),
  (2006, 7, '2026-02-17', 'paid'),
  (2007, 2, '2026-03-02', 'paid'),
  (2008, 12, '2026-03-05', 'paid'),
  (2009, 6, '2026-03-18', 'paid'),
  (2010, 1, '2026-04-07', 'paid'),
  (2011, 9, '2026-04-22', 'pending'),
  (2012, 2, '2026-05-09', 'paid'),
  (2013, 13, '2026-05-11', 'paid'),
  (2014, 8, '2026-05-30', 'paid'),
  (2015, 10, '2026-06-14', 'paid'),
  (2016, 7, '2026-06-28', 'refunded'),
  (2017, 4, '2026-07-03', 'paid'),
  (2018, 3, '2026-07-15', 'paid'),
  (2019, 2, '2026-07-29', 'paid'),
  (2020, 5, '2026-08-04', 'paid'),
  (2021, 11, '2026-08-20', 'paid'),
  (2022, 7, '2026-08-26', 'paid'),
  (2023, 6, '2026-09-03', 'paid'),
  (2024, 1, '2026-09-10', 'cancelled'),
  (2025, 9, '2026-09-19', 'paid'),
  (2026, 12, '2026-09-22', 'paid'),
  (2027, 11, '2026-09-30', 'paid'),
  (2028, 2, '2026-10-01', 'paid'),
  (2029, 4, '2026-10-04', 'paid'),
  (2030, 8, '2026-10-08', 'pending'),
  (2031, 10, '2026-10-12', 'paid'),
  (2032, 7, '2026-10-15', 'cancelled'),
  (2033, 5, '2026-10-19', 'paid'),
  (2034, 3, '2026-10-21', 'paid'),
  (2035, 13, '2026-10-22', 'paid'),
  (2036, 6, '2026-10-24', 'paid'),
  (2037, 11, '2026-10-26', 'pending');

CREATE TABLE order_items (
  order_id INTEGER NOT NULL REFERENCES orders(id),
  product_id INTEGER NOT NULL REFERENCES products(id),
  quantity INTEGER NOT NULL,
  unit_price REAL NOT NULL
);
INSERT INTO order_items VALUES
  (2001, 5, 2, 1100.00),
  (2002, 7, 3, 1900.00),
  (2003, 1, 5, 120.00),
  (2003, 2, 5, 80.00),
  (2004, 9, 10, 400.00),
  (2005, 6, 2, 1650.00),
  (2006, 7, 4, 1900.00),
  (2006, 8, 4, 780.00),
  (2007, 5, 3, 1100.00),
  (2007, 3, 3, 150.00),
  (2008, 1, 1, 120.00),
  (2009, 8, 6, 820.00),
  (2010, 6, 1, 1650.00),
  (2010, 4, 2, 110.00),
  (2011, 10, 5, 250.00),
  (2012, 7, 2, 1900.00),
  (2012, 1, 2, 135.00),
  (2013, 2, 3, 80.00),
  (2014, 9, 6, 400.00),
  (2014, 10, 6, 250.00),
  (2015, 5, 1, 1050.00),
  (2015, 2, 1, 80.00),
  (2016, 6, 2, 1650.00),
  (2017, 3, 4, 140.00),
  (2017, 4, 4, 110.00),
  (2018, 9, 8, 400.00),
  (2019, 6, 2, 1650.00),
  (2019, 2, 2, 80.00),
  (2020, 1, 3, 135.00),
  (2020, 3, 3, 140.00),
  (2021, 5, 2, 1050.00),
  (2022, 8, 2, 820.00),
  (2022, 4, 2, 110.00),
  (2023, 10, 4, 300.00),
  (2024, 7, 1, 1900.00),
  (2025, 2, 6, 80.00),
  (2025, 1, 6, 135.00),
  (2026, 3, 1, 140.00),
  (2027, 4, 3, 110.00),
  (2028, 10, 3, 300.00),
  (2029, 5, 1, 1050.00),
  (2029, 1, 1, 135.00),
  (2030, 6, 1, 1650.00),
  (2031, 2, 4, 80.00),
  (2031, 3, 2, 140.00),
  (2032, 9, 3, 400.00),
  (2033, 8, 1, 820.00),
  (2034, 1, 2, 135.00),
  (2035, 4, 1, 110.00),
  (2036, 7, 1, 1900.00),
  (2037, 2, 2, 80.00);

CREATE TABLE refunds (
  id INTEGER PRIMARY KEY,
  order_id INTEGER NOT NULL REFERENCES orders(id),
  amount REAL NOT NULL,
  refunded_on TEXT NOT NULL,
  reason TEXT NOT NULL
);
INSERT INTO refunds VALUES
  (1, 2006, 6000.00, '2026-03-01', 'chairs returned'),
  (2, 2014, 300.00, '2026-06-10', 'license overcharge'),
  (3, 2019, 160.00, '2026-08-11', 'damaged item');
