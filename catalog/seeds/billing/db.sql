-- Billing seed: a B2B SaaS billing system. Plans, customer accounts, seat usage and add-ons, invoices,
-- payment attempts, credit notes and scheduled plan changes. "Today" in this world is 2026-11-02.
CREATE TABLE plans (
  id INTEGER PRIMARY KEY,
  code TEXT NOT NULL UNIQUE,
  name TEXT NOT NULL,
  monthly_price REAL NOT NULL,
  seats_included INTEGER NOT NULL
);
INSERT INTO plans VALUES
  (1, 'starter', 'Starter', 290.00, 5),
  (2, 'pro', 'Pro', 890.00, 20),
  (3, 'business', 'Business', 2400.00, 50);

CREATE TABLE accounts (
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL,
  billing_email TEXT NOT NULL,
  region TEXT NOT NULL CHECK (region IN ('ae', 'eu', 'us')),
  plan_id INTEGER NOT NULL REFERENCES plans(id),
  status TEXT NOT NULL CHECK (status IN ('active', 'suspended', 'closed')),
  is_internal INTEGER NOT NULL DEFAULT 0,
  created_on TEXT NOT NULL
);
INSERT INTO accounts VALUES
  (1, 'Qasr Logistics', 'ap@qasrlogistics.ae', 'ae', 3, 'active', 0, '2024-03-18'),
  (2, 'Bluefin Marine', 'accounts@bluefinmarine.ae', 'ae', 2, 'active', 0, '2024-07-02'),
  (3, 'Tidewater Retail', 'finance@tidewater.eu', 'eu', 2, 'active', 0, '2024-11-25'),
  (4, 'Northgate Clinics', 'billing@northgateclinics.eu', 'eu', 1, 'active', 0, '2025-02-14'),
  (5, 'Copperleaf Studio', 'hello@copperleaf.studio', 'us', 1, 'active', 0, '2025-04-30'),
  (6, 'Saffron Kitchens', 'ops@saffronkitchens.ae', 'ae', 2, 'active', 0, '2025-05-21'),
  (7, 'Ironbridge Legal', 'payables@ironbridgelegal.eu', 'eu', 3, 'active', 0, '2025-06-09'),
  (8, 'Mirage Events', 'ops@mirage-events.ae', 'ae', 2, 'active', 0, '2025-08-12'),
  (9, 'Atlas Freight', 'ar@atlasfreight.us', 'us', 2, 'suspended', 0, '2025-09-03'),
  (10, 'Juniper Dental', 'office@juniperdental.ae', 'ae', 1, 'active', 0, '2025-12-01'),
  (11, 'Helix Robotics', 'finance@helixrobotics.us', 'us', 3, 'closed', 0, '2024-05-06'),
  (12, 'Ledgerline QA Tenant', 'qa@ledgerline.test', 'ae', 3, 'active', 1, '2024-01-02'),
  (13, 'Sales Demo Tenant', 'demo@ledgerline.test', 'eu', 2, 'active', 1, '2024-01-02'),
  (14, 'Palmyra Hotels', 'treasury@palmyrahotels.ae', 'ae', 3, 'active', 0, '2026-02-10');

CREATE TABLE seat_addons (
  id INTEGER PRIMARY KEY,
  account_id INTEGER NOT NULL REFERENCES accounts(id),
  seats INTEGER NOT NULL,
  purchased_on TEXT NOT NULL
);
INSERT INTO seat_addons VALUES
  (1, 2, 5, '2026-06-03'),
  (2, 7, 4, '2026-03-11'),
  (3, 7, 6, '2026-08-27');

CREATE TABLE seat_usage (
  account_id INTEGER NOT NULL REFERENCES accounts(id),
  measured_on TEXT NOT NULL,
  seats INTEGER NOT NULL
);
INSERT INTO seat_usage VALUES
  (1, '2026-09-28', 46), (1, '2026-10-05', 47), (1, '2026-10-12', 48), (1, '2026-10-19', 48), (1, '2026-10-26', 47),
  (2, '2026-09-28', 21), (2, '2026-10-05', 22), (2, '2026-10-12', 23), (2, '2026-10-19', 23), (2, '2026-10-26', 23),
  (3, '2026-09-28', 17), (3, '2026-10-05', 18), (3, '2026-10-12', 22), (3, '2026-10-19', 21), (3, '2026-10-26', 19),
  (4, '2026-09-28', 4), (4, '2026-10-05', 5), (4, '2026-10-12', 5), (4, '2026-10-19', 5), (4, '2026-10-26', 5),
  (5, '2026-09-28', 6), (5, '2026-10-05', 6), (5, '2026-10-12', 7), (5, '2026-10-19', 7), (5, '2026-10-26', 7),
  (6, '2026-09-28', 12), (6, '2026-10-05', 13), (6, '2026-10-12', 12), (6, '2026-10-19', 14), (6, '2026-10-26', 13),
  (7, '2026-09-28', 54), (7, '2026-10-05', 55), (7, '2026-10-12', 57), (7, '2026-10-19', 58), (7, '2026-10-26', 56),
  (8, '2026-09-28', 15), (8, '2026-10-05', 14), (8, '2026-10-12', 14), (8, '2026-10-19', 12), (8, '2026-10-26', 11),
  (9, '2026-09-28', 24), (9, '2026-10-05', 26), (9, '2026-10-12', 26), (9, '2026-10-19', 25), (9, '2026-10-26', 25),
  (10, '2026-09-28', 4), (10, '2026-10-05', 4), (10, '2026-10-12', 3), (10, '2026-10-19', 4), (10, '2026-10-26', 4),
  (11, '2026-08-31', 61), (11, '2026-09-07', 70), (11, '2026-09-14', 12),
  (12, '2026-09-28', 78), (12, '2026-10-05', 80), (12, '2026-10-12', 80), (12, '2026-10-19', 79), (12, '2026-10-26', 80),
  (13, '2026-09-28', 22), (13, '2026-10-05', 25), (13, '2026-10-12', 24), (13, '2026-10-19', 25), (13, '2026-10-26', 23),
  (14, '2026-09-21', 53), (14, '2026-09-28', 51), (14, '2026-10-05', 47), (14, '2026-10-12', 49), (14, '2026-10-19', 44),
  (14, '2026-10-26', 46);

CREATE TABLE invoices (
  id INTEGER PRIMARY KEY,
  account_id INTEGER NOT NULL REFERENCES accounts(id),
  period TEXT NOT NULL,              -- billing month, YYYY-MM
  issued_on TEXT,                    -- NULL while the invoice is a draft
  due_on TEXT NOT NULL,
  amount REAL NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('draft', 'open', 'overdue', 'paid', 'void'))
);
INSERT INTO invoices VALUES
  (5001, 1, '2026-09', '2026-09-01', '2026-09-15', 2400.00, 'paid'),
  (5002, 2, '2026-09', '2026-09-01', '2026-09-15', 890.00, 'paid'),
  (5003, 3, '2026-09', '2026-09-01', '2026-09-15', 890.00, 'paid'),
  (5004, 4, '2026-09', '2026-09-01', '2026-09-15', 290.00, 'paid'),
  (5005, 5, '2026-09', '2026-09-01', '2026-09-15', 290.00, 'paid'),
  (5006, 6, '2026-09', '2026-09-01', '2026-09-15', 890.00, 'paid'),
  (5007, 7, '2026-09', '2026-09-01', '2026-09-15', 2400.00, 'paid'),
  (5008, 8, '2026-09', '2026-09-01', '2026-09-15', 890.00, 'paid'),
  (5009, 9, '2026-09', '2026-09-01', '2026-09-15', 890.00, 'open'),
  (5010, 10, '2026-09', '2026-09-01', '2026-09-15', 290.00, 'paid'),
  (5011, 11, '2026-09', '2026-09-01', '2026-09-15', 2400.00, 'void'),
  (5013, 13, '2026-09', '2026-09-01', '2026-09-15', 890.00, 'paid'),
  (5014, 14, '2026-09', '2026-09-01', '2026-09-15', 2400.00, 'paid'),
  (5101, 1, '2026-10', '2026-10-01', '2026-10-15', 2400.00, 'paid'),
  (5102, 2, '2026-10', '2026-10-01', '2026-10-15', 890.00, 'open'),
  (5103, 3, '2026-10', '2026-10-01', '2026-10-15', 890.00, 'open'),
  (5104, 4, '2026-10', '2026-10-01', '2026-10-15', 290.00, 'paid'),
  (5105, 5, '2026-10', '2026-10-01', '2026-10-15', 380.00, 'paid'),
  (5106, 6, '2026-10', '2026-10-01', '2026-10-15', 890.00, 'open'),
  (5107, 7, '2026-10', '2026-10-01', '2026-10-15', 2400.00, 'paid'),
  (5108, 8, '2026-10', '2026-10-01', '2026-10-15', 890.00, 'paid'),
  (5109, 9, '2026-10', '2026-10-01', '2026-10-15', 890.00, 'open'),
  (5110, 10, '2026-10', '2026-10-03', '2026-11-02', 290.00, 'open'),
  (5111, 13, '2026-10', '2026-10-01', '2026-10-15', 890.00, 'paid'),
  (5112, 14, '2026-10', '2026-10-01', '2026-11-14', 2400.00, 'open'),
  (5201, 1, '2026-10', NULL, '2026-10-25', 600.00, 'draft');

CREATE TABLE payments (
  id INTEGER PRIMARY KEY,
  invoice_id INTEGER NOT NULL REFERENCES invoices(id),
  received_at TEXT NOT NULL,         -- YYYY-MM-DD HH:MM:SS, UTC
  amount REAL NOT NULL,
  result TEXT NOT NULL CHECK (result IN ('succeeded', 'failed'))
);
INSERT INTO payments VALUES
  (1, 5001, '2026-09-09 08:14:02', 2400.00, 'succeeded'),
  (2, 5002, '2026-09-11 13:40:51', 890.00, 'succeeded'),
  (3, 5003, '2026-09-14 09:02:17', 890.00, 'succeeded'),
  (4, 5004, '2026-09-05 16:21:44', 290.00, 'succeeded'),
  (5, 5005, '2026-09-12 11:09:30', 290.00, 'succeeded'),
  (6, 5006, '2026-09-15 07:55:12', 890.00, 'succeeded'),
  (7, 5007, '2026-09-18 10:31:08', 2400.00, 'succeeded'),
  (8, 5008, '2026-09-13 19:12:45', 890.00, 'succeeded'),
  (9, 5009, '2026-09-15 06:00:03', 890.00, 'failed'),
  (10, 5010, '2026-09-10 12:44:19', 290.00, 'succeeded'),
  (11, 5013, '2026-09-02 09:00:00', 890.00, 'succeeded'),
  (12, 5014, '2026-10-02 10:05:37', 2400.00, 'succeeded'),
  (13, 5111, '2026-10-03 09:00:00', 890.00, 'succeeded'),
  (14, 5104, '2026-10-06 14:18:26', 290.00, 'succeeded'),
  (15, 5101, '2026-10-09 08:47:55', 2400.00, 'succeeded'),
  (16, 5105, '2026-10-11 17:33:02', 380.00, 'succeeded'),
  (17, 5102, '2026-10-13 10:26:41', 400.00, 'succeeded'),
  (18, 5106, '2026-10-14 15:02:09', 800.00, 'succeeded'),
  (19, 5103, '2026-10-15 06:00:04', 890.00, 'failed'),
  (20, 5109, '2026-10-15 06:00:05', 890.00, 'failed'),
  (21, 5107, '2026-10-20 11:58:13', 2400.00, 'succeeded'),
  (22, 5108, '2026-10-31 18:47:20', 890.00, 'succeeded');

CREATE TABLE credit_notes (
  id INTEGER PRIMARY KEY,
  invoice_id INTEGER NOT NULL REFERENCES invoices(id),
  amount REAL NOT NULL,
  issued_on TEXT NOT NULL,
  reason TEXT NOT NULL
);
INSERT INTO credit_notes VALUES
  (1, 5003, 50.00, '2026-09-20', 'duplicate seat charge'),
  (2, 5106, 90.00, '2026-10-10', 'onboarding discount'),
  (3, 5107, 240.00, '2026-10-24', 'outage-2026-10');

CREATE TABLE plan_changes (
  id INTEGER PRIMARY KEY,
  account_id INTEGER NOT NULL REFERENCES accounts(id),
  from_plan_id INTEGER NOT NULL REFERENCES plans(id),
  to_plan_id INTEGER NOT NULL REFERENCES plans(id),
  effective_on TEXT NOT NULL,
  requested_on TEXT
);
INSERT INTO plan_changes VALUES
  (1, 6, 1, 2, '2026-05-21', '2026-05-21'),
  (2, 4, 2, 1, '2026-03-01', '2026-02-17');
