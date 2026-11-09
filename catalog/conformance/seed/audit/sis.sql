BEGIN TRANSACTION;
CREATE TABLE "catalog_edition" (
  "edition_id" TEXT NOT NULL,
  "name" TEXT NOT NULL,
  "from_term" TEXT NOT NULL,
  "to_term" TEXT,
  "doc_id" TEXT NOT NULL,
  "status" TEXT NOT NULL,
  CONSTRAINT "pk_catalog_edition" PRIMARY KEY ("edition_id")
);
INSERT INTO "catalog_edition" VALUES('CAT-2022','Academic Catalog 2022','2022FA','2025SU','doc-001','in_force_for_its_terms');
INSERT INTO "catalog_edition" VALUES('CAT-2025','Academic Catalog 2025','2025FA',NULL,'doc-002','in_force');
CREATE TABLE "course" (
  "course_code" TEXT NOT NULL,
  "title" TEXT NOT NULL,
  "dept" TEXT NOT NULL,
  "capstone" INTEGER NOT NULL,
  CONSTRAINT "pk_course" PRIMARY KEY ("course_code")
);
INSERT INTO "course" VALUES('ACCT201','Financial Accounting','ACCT',0);
INSERT INTO "course" VALUES('ACCT202','Managerial Accounting','ACCT',0);
INSERT INTO "course" VALUES('ARTS110','Art Appreciation','ARTS',0);
INSERT INTO "course" VALUES('BIOL101','General Biology','BIOL',0);
INSERT INTO "course" VALUES('BUSA210','Spreadsheet Modelling','BUSA',0);
INSERT INTO "course" VALUES('BUSA310','Business Forecasting','BUSA',0);
INSERT INTO "course" VALUES('BUSA320','Decision Analytics','BUSA',0);
INSERT INTO "course" VALUES('BUSA330','Business Analytics Methods','BUSA',0);
INSERT INTO "course" VALUES('BUSA420','Marketing Analytics','BUSA',0);
INSERT INTO "course" VALUES('BUSA430','People Analytics','BUSA',0);
INSERT INTO "course" VALUES('BUSA440','Financial Analytics','BUSA',0);
INSERT INTO "course" VALUES('BUSA450','Business Capstone','BUSA',1);
INSERT INTO "course" VALUES('BUSL210','Business Law','BUSL',0);
INSERT INTO "course" VALUES('CIS220','Business Information Systems','MIS',0);
INSERT INTO "course" VALUES('COMP101','Introduction to Programming','COMP',0);
INSERT INTO "course" VALUES('COMP201','Data Structures','COMP',0);
INSERT INTO "course" VALUES('COMP210','Object-Oriented Programming','COMP',0);
INSERT INTO "course" VALUES('COMP220','Software Engineering I','COMP',0);
INSERT INTO "course" VALUES('COMP230','Web Development','COMP',0);
INSERT INTO "course" VALUES('COMP250','Systems Programming','COMP',0);
INSERT INTO "course" VALUES('COMP251','Computer Organisation','COMP',0);
INSERT INTO "course" VALUES('COMP252','Operating Systems','COMP',0);
INSERT INTO "course" VALUES('COMP301','Algorithms','COMP',0);
INSERT INTO "course" VALUES('COMP310','Theory of Computation','COMP',0);
INSERT INTO "course" VALUES('COMP315','Computer Architecture','COMP',0);
INSERT INTO "course" VALUES('COMP320','Software Engineering II','COMP',0);
INSERT INTO "course" VALUES('COMP340','Database Systems','COMP',0);
INSERT INTO "course" VALUES('COMP395','Computing Seminar','COMP',0);
INSERT INTO "course" VALUES('COMP400','Capstone Project','COMP',1);
INSERT INTO "course" VALUES('COMP410','Computer Networks','COMP',0);
INSERT INTO "course" VALUES('COMP420','Cyber Security','COMP',0);
INSERT INTO "course" VALUES('COMP430','Machine Learning for Computing','COMP',0);
INSERT INTO "course" VALUES('COMP440','Cloud Computing','COMP',0);
INSERT INTO "course" VALUES('COMP450','Mobile Development','COMP',0);
INSERT INTO "course" VALUES('COMP460','Computer Graphics','COMP',0);
INSERT INTO "course" VALUES('CSC101','Introduction to Programming (2022 code)','COMP',0);
INSERT INTO "course" VALUES('CSC201','Data Structures (2022 code)','COMP',0);
INSERT INTO "course" VALUES('DSCI200','Data Wrangling I','DSCI',0);
INSERT INTO "course" VALUES('DSCI205','Data Wrangling II','DSCI',0);
INSERT INTO "course" VALUES('DSCI210','Data Wrangling','DSCI',0);
INSERT INTO "course" VALUES('DSCI220','Data Management','DSCI',0);
INSERT INTO "course" VALUES('DSCI230','Statistical Programming','DSCI',0);
INSERT INTO "course" VALUES('DSCI310','Regression Modelling','DSCI',0);
INSERT INTO "course" VALUES('DSCI320','Applied Statistics','DSCI',0);
INSERT INTO "course" VALUES('DSCI330','Data Mining','DSCI',0);
INSERT INTO "course" VALUES('DSCI395','Data Science Seminar','DSCI',0);
INSERT INTO "course" VALUES('DSCI410','Machine Learning','DSCI',0);
INSERT INTO "course" VALUES('DSCI430','Natural Language Processing','DSCI',0);
INSERT INTO "course" VALUES('DSCI440','Recommender Systems','DSCI',0);
INSERT INTO "course" VALUES('DSCI450','Data Visualisation','DSCI',0);
INSERT INTO "course" VALUES('DSCI460','Causal Inference','DSCI',0);
INSERT INTO "course" VALUES('ECON101','Microeconomics','ECON',0);
INSERT INTO "course" VALUES('ECON102','Macroeconomics','ECON',0);
INSERT INTO "course" VALUES('ECON301','Managerial Economics','ECON',0);
INSERT INTO "course" VALUES('ENGL101','Academic Writing','ENGL',0);
INSERT INTO "course" VALUES('ENGL102','Research Writing (advanced English)','ENGL',0);
INSERT INTO "course" VALUES('ENVS101','Environmental Science and Sustainability','ENVS',0);
INSERT INTO "course" VALUES('FIN301','Corporate Finance','FIN',0);
INSERT INTO "course" VALUES('FIN310','Investments','FIN',0);
INSERT INTO "course" VALUES('FIN410','Financial Modelling','FIN',0);
INSERT INTO "course" VALUES('HUMN110','Ethics','HUMN',0);
INSERT INTO "course" VALUES('HUMN120','Arts and Culture','HUMN',0);
INSERT INTO "course" VALUES('INNO201','Innovation and Entrepreneurship','INNO',0);
INSERT INTO "course" VALUES('INTR390','Internship','INTR',1);
INSERT INTO "course" VALUES('IT220','Information Technology for Business','MIS',0);
INSERT INTO "course" VALUES('MATH101','Calculus I','MATH',0);
INSERT INTO "course" VALUES('MATH102','Calculus II','MATH',0);
INSERT INTO "course" VALUES('MATH210','Discrete Mathematics','MATH',0);
INSERT INTO "course" VALUES('MGMT201','Principles of Management','MGMT',0);
INSERT INTO "course" VALUES('MGMT310','Organisational Behaviour','MGMT',0);
INSERT INTO "course" VALUES('MGMT320','Operations Management','MGMT',0);
INSERT INTO "course" VALUES('MGMT410','Strategic Management','MGMT',0);
INSERT INTO "course" VALUES('MGMT430','Negotiation','MGMT',0);
INSERT INTO "course" VALUES('MIS340','Database Systems (cross-listed)','MIS',0);
INSERT INTO "course" VALUES('MKTG201','Principles of Marketing','MKTG',0);
INSERT INTO "course" VALUES('MKTG310','Consumer Behaviour','MKTG',0);
INSERT INTO "course" VALUES('MKTG410','Digital Marketing','MKTG',0);
INSERT INTO "course" VALUES('PHYS101','Physics I','PHYS',0);
INSERT INTO "course" VALUES('PHYS101L','Physics I Lab','PHYS',0);
INSERT INTO "course" VALUES('PSYC101','Introduction to Psychology','PSYC',0);
INSERT INTO "course" VALUES('QM210','Business Statistics','BUSA',0);
INSERT INTO "course" VALUES('SOCI110','Introduction to Sociology','SOCI',0);
INSERT INTO "course" VALUES('STAT201','Probability and Statistics','STAT',0);
INSERT INTO "course" VALUES('UAES101','Emirati Studies','UAES',0);
CREATE TABLE "course_relation" (
  "relation_id" TEXT NOT NULL,
  "kind" TEXT NOT NULL,
  "from_codes" TEXT NOT NULL,
  "to_codes" TEXT NOT NULL,
  "effective_term" TEXT NOT NULL,
  "approval_ref" TEXT,
  "note" TEXT,
  CONSTRAINT "pk_course_relation" PRIMARY KEY ("relation_id")
);
INSERT INTO "course_relation" VALUES('R-01','renumbering','CSC101','COMP101','2025FA',NULL,'Same course; taking both is a repeat');
INSERT INTO "course_relation" VALUES('R-02','renumbering','CSC201','COMP201','2025FA',NULL,'Same course; taking both is a repeat');
INSERT INTO "course_relation" VALUES('R-03','equivalency','COMP430','DSCI410','2024FA','SA-2024-07','Approved equivalency; one direction only');
INSERT INTO "course_relation" VALUES('R-04','cross_list','COMP340','MIS340','2022FA',NULL,'Same course under two codes; counts once (P-14)');
INSERT INTO "course_relation" VALUES('R-05','split','COMP250','COMP251, COMP252','2025FA',NULL,'The old course meets both new lines');
INSERT INTO "course_relation" VALUES('R-06','merge','DSCI200, DSCI205','DSCI210','2025FA',NULL,'Both old courses needed; one alone goes to free electives');
INSERT INTO "course_relation" VALUES('R-07','credit_change','COMP400','COMP400','2025FA',NULL,'Plan line decides the credits (P-25)');
INSERT INTO "course_relation" VALUES('R-09','syllabus_version','STAT201','STAT201','2025FA',NULL,'Recorded and echoed only');
INSERT INTO "course_relation" VALUES('R-10','retired','CIS220','IT220','2025FA',NULL,'A late finisher needs a per-student substitution');
CREATE TABLE "course_version" (
  "course_code" TEXT NOT NULL,
  "edition_id" TEXT NOT NULL,
  "credits" INTEGER NOT NULL,
  "syllabus_version" TEXT,
  CONSTRAINT "pk_course_version" PRIMARY KEY ("course_code", "edition_id")
);
INSERT INTO "course_version" VALUES('ACCT201','CAT-2022',3,NULL);
INSERT INTO "course_version" VALUES('ACCT201','CAT-2025',3,NULL);
INSERT INTO "course_version" VALUES('ACCT202','CAT-2022',3,NULL);
INSERT INTO "course_version" VALUES('ACCT202','CAT-2025',3,NULL);
INSERT INTO "course_version" VALUES('ARTS110','CAT-2022',3,NULL);
INSERT INTO "course_version" VALUES('ARTS110','CAT-2025',3,NULL);
INSERT INTO "course_version" VALUES('BIOL101','CAT-2022',3,NULL);
INSERT INTO "course_version" VALUES('BIOL101','CAT-2025',3,NULL);
INSERT INTO "course_version" VALUES('BUSA210','CAT-2025',3,NULL);
INSERT INTO "course_version" VALUES('BUSA310','CAT-2025',3,NULL);
INSERT INTO "course_version" VALUES('BUSA320','CAT-2025',3,NULL);
INSERT INTO "course_version" VALUES('BUSA330','CAT-2022',3,NULL);
INSERT INTO "course_version" VALUES('BUSA330','CAT-2025',3,NULL);
INSERT INTO "course_version" VALUES('BUSA420','CAT-2025',3,NULL);
INSERT INTO "course_version" VALUES('BUSA430','CAT-2025',3,NULL);
INSERT INTO "course_version" VALUES('BUSA440','CAT-2025',3,NULL);
INSERT INTO "course_version" VALUES('BUSA450','CAT-2022',3,NULL);
INSERT INTO "course_version" VALUES('BUSA450','CAT-2025',3,NULL);
INSERT INTO "course_version" VALUES('BUSL210','CAT-2022',3,NULL);
INSERT INTO "course_version" VALUES('BUSL210','CAT-2025',3,NULL);
INSERT INTO "course_version" VALUES('CIS220','CAT-2022',3,NULL);
INSERT INTO "course_version" VALUES('COMP101','CAT-2025',3,NULL);
INSERT INTO "course_version" VALUES('COMP201','CAT-2025',3,NULL);
INSERT INTO "course_version" VALUES('COMP210','CAT-2022',3,NULL);
INSERT INTO "course_version" VALUES('COMP210','CAT-2025',3,NULL);
INSERT INTO "course_version" VALUES('COMP220','CAT-2022',3,NULL);
INSERT INTO "course_version" VALUES('COMP220','CAT-2025',3,NULL);
INSERT INTO "course_version" VALUES('COMP230','CAT-2022',3,NULL);
INSERT INTO "course_version" VALUES('COMP230','CAT-2025',3,NULL);
INSERT INTO "course_version" VALUES('COMP250','CAT-2022',4,NULL);
INSERT INTO "course_version" VALUES('COMP251','CAT-2025',3,NULL);
INSERT INTO "course_version" VALUES('COMP252','CAT-2025',3,NULL);
INSERT INTO "course_version" VALUES('COMP301','CAT-2022',3,NULL);
INSERT INTO "course_version" VALUES('COMP301','CAT-2025',3,NULL);
INSERT INTO "course_version" VALUES('COMP310','CAT-2022',3,NULL);
INSERT INTO "course_version" VALUES('COMP310','CAT-2025',3,NULL);
INSERT INTO "course_version" VALUES('COMP315','CAT-2022',3,NULL);
INSERT INTO "course_version" VALUES('COMP315','CAT-2025',3,NULL);
INSERT INTO "course_version" VALUES('COMP320','CAT-2022',3,NULL);
INSERT INTO "course_version" VALUES('COMP320','CAT-2025',3,NULL);
INSERT INTO "course_version" VALUES('COMP340','CAT-2022',3,NULL);
INSERT INTO "course_version" VALUES('COMP340','CAT-2025',3,NULL);
INSERT INTO "course_version" VALUES('COMP395','CAT-2022',2,NULL);
INSERT INTO "course_version" VALUES('COMP395','CAT-2025',2,NULL);
INSERT INTO "course_version" VALUES('COMP400','CAT-2022',3,NULL);
INSERT INTO "course_version" VALUES('COMP400','CAT-2025',4,NULL);
INSERT INTO "course_version" VALUES('COMP410','CAT-2022',3,NULL);
INSERT INTO "course_version" VALUES('COMP410','CAT-2025',3,NULL);
INSERT INTO "course_version" VALUES('COMP420','CAT-2022',3,NULL);
INSERT INTO "course_version" VALUES('COMP420','CAT-2025',3,NULL);
INSERT INTO "course_version" VALUES('COMP430','CAT-2022',3,NULL);
INSERT INTO "course_version" VALUES('COMP430','CAT-2025',3,NULL);
INSERT INTO "course_version" VALUES('COMP440','CAT-2022',3,NULL);
INSERT INTO "course_version" VALUES('COMP440','CAT-2025',3,NULL);
INSERT INTO "course_version" VALUES('COMP450','CAT-2022',3,NULL);
INSERT INTO "course_version" VALUES('COMP450','CAT-2025',3,NULL);
INSERT INTO "course_version" VALUES('COMP460','CAT-2022',3,NULL);
INSERT INTO "course_version" VALUES('COMP460','CAT-2025',3,NULL);
INSERT INTO "course_version" VALUES('CSC101','CAT-2022',3,NULL);
INSERT INTO "course_version" VALUES('CSC201','CAT-2022',3,NULL);
INSERT INTO "course_version" VALUES('DSCI200','CAT-2022',4,NULL);
INSERT INTO "course_version" VALUES('DSCI205','CAT-2022',2,NULL);
INSERT INTO "course_version" VALUES('DSCI210','CAT-2025',3,NULL);
INSERT INTO "course_version" VALUES('DSCI220','CAT-2022',3,NULL);
INSERT INTO "course_version" VALUES('DSCI220','CAT-2025',3,NULL);
INSERT INTO "course_version" VALUES('DSCI230','CAT-2022',3,NULL);
INSERT INTO "course_version" VALUES('DSCI230','CAT-2025',3,NULL);
INSERT INTO "course_version" VALUES('DSCI310','CAT-2022',3,NULL);
INSERT INTO "course_version" VALUES('DSCI310','CAT-2025',3,NULL);
INSERT INTO "course_version" VALUES('DSCI320','CAT-2022',3,NULL);
INSERT INTO "course_version" VALUES('DSCI320','CAT-2025',3,NULL);
INSERT INTO "course_version" VALUES('DSCI330','CAT-2022',3,NULL);
INSERT INTO "course_version" VALUES('DSCI330','CAT-2025',3,NULL);
INSERT INTO "course_version" VALUES('DSCI395','CAT-2022',2,NULL);
INSERT INTO "course_version" VALUES('DSCI395','CAT-2025',2,NULL);
INSERT INTO "course_version" VALUES('DSCI410','CAT-2022',3,NULL);
INSERT INTO "course_version" VALUES('DSCI410','CAT-2025',3,NULL);
INSERT INTO "course_version" VALUES('DSCI430','CAT-2022',3,NULL);
INSERT INTO "course_version" VALUES('DSCI430','CAT-2025',3,NULL);
INSERT INTO "course_version" VALUES('DSCI440','CAT-2022',3,NULL);
INSERT INTO "course_version" VALUES('DSCI440','CAT-2025',3,NULL);
INSERT INTO "course_version" VALUES('DSCI450','CAT-2022',3,NULL);
INSERT INTO "course_version" VALUES('DSCI450','CAT-2025',3,NULL);
INSERT INTO "course_version" VALUES('DSCI460','CAT-2022',3,NULL);
INSERT INTO "course_version" VALUES('DSCI460','CAT-2025',3,NULL);
INSERT INTO "course_version" VALUES('ECON101','CAT-2022',3,NULL);
INSERT INTO "course_version" VALUES('ECON101','CAT-2025',3,NULL);
INSERT INTO "course_version" VALUES('ECON102','CAT-2022',3,NULL);
INSERT INTO "course_version" VALUES('ECON102','CAT-2025',3,NULL);
INSERT INTO "course_version" VALUES('ECON301','CAT-2022',3,NULL);
INSERT INTO "course_version" VALUES('ECON301','CAT-2025',3,NULL);
INSERT INTO "course_version" VALUES('ENGL101','CAT-2022',3,NULL);
INSERT INTO "course_version" VALUES('ENGL101','CAT-2025',3,NULL);
INSERT INTO "course_version" VALUES('ENGL102','CAT-2022',3,NULL);
INSERT INTO "course_version" VALUES('ENGL102','CAT-2025',3,NULL);
INSERT INTO "course_version" VALUES('ENVS101','CAT-2022',3,NULL);
INSERT INTO "course_version" VALUES('ENVS101','CAT-2025',3,NULL);
INSERT INTO "course_version" VALUES('FIN301','CAT-2022',3,NULL);
INSERT INTO "course_version" VALUES('FIN301','CAT-2025',3,NULL);
INSERT INTO "course_version" VALUES('FIN310','CAT-2022',3,NULL);
INSERT INTO "course_version" VALUES('FIN310','CAT-2025',3,NULL);
INSERT INTO "course_version" VALUES('FIN410','CAT-2022',3,NULL);
INSERT INTO "course_version" VALUES('FIN410','CAT-2025',3,NULL);
INSERT INTO "course_version" VALUES('HUMN110','CAT-2022',3,NULL);
INSERT INTO "course_version" VALUES('HUMN110','CAT-2025',3,NULL);
INSERT INTO "course_version" VALUES('HUMN120','CAT-2022',3,NULL);
INSERT INTO "course_version" VALUES('HUMN120','CAT-2025',3,NULL);
INSERT INTO "course_version" VALUES('INNO201','CAT-2022',3,NULL);
INSERT INTO "course_version" VALUES('INNO201','CAT-2025',3,NULL);
INSERT INTO "course_version" VALUES('INTR390','CAT-2022',6,NULL);
INSERT INTO "course_version" VALUES('INTR390','CAT-2025',6,NULL);
INSERT INTO "course_version" VALUES('IT220','CAT-2025',3,NULL);
INSERT INTO "course_version" VALUES('MATH101','CAT-2022',3,NULL);
INSERT INTO "course_version" VALUES('MATH101','CAT-2025',3,NULL);
INSERT INTO "course_version" VALUES('MATH102','CAT-2022',3,NULL);
INSERT INTO "course_version" VALUES('MATH102','CAT-2025',3,NULL);
INSERT INTO "course_version" VALUES('MATH210','CAT-2022',3,NULL);
INSERT INTO "course_version" VALUES('MATH210','CAT-2025',3,NULL);
INSERT INTO "course_version" VALUES('MGMT201','CAT-2022',3,NULL);
INSERT INTO "course_version" VALUES('MGMT201','CAT-2025',3,NULL);
INSERT INTO "course_version" VALUES('MGMT310','CAT-2022',3,NULL);
INSERT INTO "course_version" VALUES('MGMT310','CAT-2025',3,NULL);
INSERT INTO "course_version" VALUES('MGMT320','CAT-2022',3,NULL);
INSERT INTO "course_version" VALUES('MGMT320','CAT-2025',3,NULL);
INSERT INTO "course_version" VALUES('MGMT410','CAT-2022',3,NULL);
INSERT INTO "course_version" VALUES('MGMT410','CAT-2025',3,NULL);
INSERT INTO "course_version" VALUES('MGMT430','CAT-2022',3,NULL);
INSERT INTO "course_version" VALUES('MGMT430','CAT-2025',3,NULL);
INSERT INTO "course_version" VALUES('MIS340','CAT-2022',3,NULL);
INSERT INTO "course_version" VALUES('MIS340','CAT-2025',3,NULL);
INSERT INTO "course_version" VALUES('MKTG201','CAT-2022',3,NULL);
INSERT INTO "course_version" VALUES('MKTG201','CAT-2025',3,NULL);
INSERT INTO "course_version" VALUES('MKTG310','CAT-2022',3,NULL);
INSERT INTO "course_version" VALUES('MKTG310','CAT-2025',3,NULL);
INSERT INTO "course_version" VALUES('MKTG410','CAT-2022',3,NULL);
INSERT INTO "course_version" VALUES('MKTG410','CAT-2025',3,NULL);
INSERT INTO "course_version" VALUES('PHYS101','CAT-2022',3,NULL);
INSERT INTO "course_version" VALUES('PHYS101','CAT-2025',3,NULL);
INSERT INTO "course_version" VALUES('PHYS101L','CAT-2022',1,NULL);
INSERT INTO "course_version" VALUES('PHYS101L','CAT-2025',1,NULL);
INSERT INTO "course_version" VALUES('PSYC101','CAT-2022',3,NULL);
INSERT INTO "course_version" VALUES('PSYC101','CAT-2025',3,NULL);
INSERT INTO "course_version" VALUES('QM210','CAT-2022',3,NULL);
INSERT INTO "course_version" VALUES('QM210','CAT-2025',3,NULL);
INSERT INTO "course_version" VALUES('SOCI110','CAT-2022',3,NULL);
INSERT INTO "course_version" VALUES('SOCI110','CAT-2025',3,NULL);
INSERT INTO "course_version" VALUES('STAT201','CAT-2022',3,'v1');
INSERT INTO "course_version" VALUES('STAT201','CAT-2025',3,'v2');
INSERT INTO "course_version" VALUES('UAES101','CAT-2022',3,NULL);
INSERT INTO "course_version" VALUES('UAES101','CAT-2025',3,NULL);
CREATE TABLE "degree_progress" (
  "student_id" TEXT NOT NULL,
  "requirement_id" TEXT NOT NULL,
  "line_id" TEXT NOT NULL,
  "status" TEXT NOT NULL,
  "required" REAL,
  "earned" REAL,
  CONSTRAINT "pk_degree_progress" PRIMARY KEY ("student_id", "requirement_id", "line_id")
);
INSERT INTO "degree_progress" VALUES('S20221356','MAJOR-CORE','','PASS',51.0,52.0);
INSERT INTO "degree_progress" VALUES('S20221356','MAJOR-ELEC','','PASS',12.0,12.0);
INSERT INTO "degree_progress" VALUES('S20221356','FREE-ELEC','','FAIL',21.0,20.0);
INSERT INTO "degree_progress" VALUES('S20221356','GENED','','PASS',30.0,30.0);
INSERT INTO "degree_progress" VALUES('S20221356','TOTAL','','PASS',120.0,120.0);
INSERT INTO "degree_progress" VALUES('S20222001','MAJOR-CORE','','PASS',51.0,51.0);
INSERT INTO "degree_progress" VALUES('S20222001','MAJOR-ELEC','','PASS',12.0,12.0);
INSERT INTO "degree_progress" VALUES('S20222001','FREE-ELEC','','PASS',21.0,21.0);
INSERT INTO "degree_progress" VALUES('S20222001','GENED','','PASS',30.0,30.0);
INSERT INTO "degree_progress" VALUES('S20222001','TOTAL','','PASS',120.0,120.0);
INSERT INTO "degree_progress" VALUES('S20231047','MAJOR-CORE','','PASS',51.0,51.0);
INSERT INTO "degree_progress" VALUES('S20231047','MAJOR-ELEC','','FAIL',12.0,9.0);
INSERT INTO "degree_progress" VALUES('S20231047','FREE-ELEC','','PASS',21.0,25.0);
INSERT INTO "degree_progress" VALUES('S20231047','GENED','','PASS',30.0,30.0);
INSERT INTO "degree_progress" VALUES('S20231047','TOTAL','','PASS',120.0,121.0);
CREATE TABLE "elective_group" (
  "group_id" TEXT NOT NULL,
  "version_id" TEXT NOT NULL,
  "requirement_id" TEXT NOT NULL,
  "title" TEXT NOT NULL,
  "rule" TEXT NOT NULL,
  "min_credits" INTEGER,
  "min_courses" INTEGER,
  "gened_area" TEXT,
  "any_course" INTEGER NOT NULL,
  CONSTRAINT "pk_elective_group" PRIMARY KEY ("group_id")
);
INSERT INTO "elective_group" VALUES('BBA-2022:GE-HUM','BBA-2022','GENED','Humanities and fine arts','min_courses',NULL,1,'humanities',0);
INSERT INTO "elective_group" VALUES('BBA-2022:GE-SOC','BBA-2022','GENED','Social and behavioural sciences','min_courses',NULL,1,'social',0);
INSERT INTO "elective_group" VALUES('BBA-2022:GE-NAT','BBA-2022','GENED','Natural science','min_courses',NULL,1,'natural_math',0);
INSERT INTO "elective_group" VALUES('BBA-2022:GE-MATH','BBA-2022','GENED','Mathematics','min_courses',NULL,1,'natural_math',0);
INSERT INTO "elective_group" VALUES('BBA-2022:GE-TOTAL','BBA-2022','GENED','Gen Ed total','min_credits',30,NULL,NULL,0);
INSERT INTO "elective_group" VALUES('BBA-2022:E01','BBA-2022','MAJOR-ELEC','Major electives','min_credits',15,NULL,NULL,0);
INSERT INTO "elective_group" VALUES('BBA-2022:F01','BBA-2022','FREE-ELEC','Free electives','min_credits',24,NULL,NULL,1);
INSERT INTO "elective_group" VALUES('BBA-2025:GE-HUM','BBA-2025','GENED','Humanities and fine arts','min_courses',NULL,1,'humanities',0);
INSERT INTO "elective_group" VALUES('BBA-2025:GE-SOC','BBA-2025','GENED','Social and behavioural sciences','min_courses',NULL,1,'social',0);
INSERT INTO "elective_group" VALUES('BBA-2025:GE-NAT','BBA-2025','GENED','Natural science and sustainability','min_courses',NULL,1,'natural_math',0);
INSERT INTO "elective_group" VALUES('BBA-2025:GE-MATH','BBA-2025','GENED','Mathematics','min_courses',NULL,1,'natural_math',0);
INSERT INTO "elective_group" VALUES('BBA-2025:GE-TOTAL','BBA-2025','GENED','Gen Ed total','min_credits',30,NULL,NULL,0);
INSERT INTO "elective_group" VALUES('BBA-2025:E01','BBA-2025','MAJOR-ELEC','Major electives','min_credits',12,NULL,NULL,0);
INSERT INTO "elective_group" VALUES('BBA-2025:F01','BBA-2025','FREE-ELEC','Free electives','min_credits',24,NULL,NULL,1);
INSERT INTO "elective_group" VALUES('BSBAN-2025:GE-HUM','BSBAN-2025','GENED','Humanities and fine arts','min_courses',NULL,1,'humanities',0);
INSERT INTO "elective_group" VALUES('BSBAN-2025:GE-SOC','BSBAN-2025','GENED','Social and behavioural sciences','min_courses',NULL,1,'social',0);
INSERT INTO "elective_group" VALUES('BSBAN-2025:GE-NAT','BSBAN-2025','GENED','Natural science and sustainability','min_courses',NULL,1,'natural_math',0);
INSERT INTO "elective_group" VALUES('BSBAN-2025:GE-MATH','BSBAN-2025','GENED','Mathematics','min_courses',NULL,1,'natural_math',0);
INSERT INTO "elective_group" VALUES('BSBAN-2025:GE-TOTAL','BSBAN-2025','GENED','Gen Ed total','min_credits',30,NULL,NULL,0);
INSERT INTO "elective_group" VALUES('BSBAN-2025:E01','BSBAN-2025','MAJOR-ELEC','Major electives','min_credits',12,NULL,NULL,0);
INSERT INTO "elective_group" VALUES('BSBAN-2025:F01','BSBAN-2025','FREE-ELEC','Free electives','min_credits',21,NULL,NULL,1);
INSERT INTO "elective_group" VALUES('BSCS-2022:GE-HUM','BSCS-2022','GENED','Humanities and fine arts','min_courses',NULL,1,'humanities',0);
INSERT INTO "elective_group" VALUES('BSCS-2022:GE-SOC','BSCS-2022','GENED','Social and behavioural sciences','min_courses',NULL,1,'social',0);
INSERT INTO "elective_group" VALUES('BSCS-2022:GE-NAT','BSCS-2022','GENED','Natural science','min_courses',NULL,1,'natural_math',0);
INSERT INTO "elective_group" VALUES('BSCS-2022:GE-MATH','BSCS-2022','GENED','Mathematics','min_courses',NULL,1,'natural_math',0);
INSERT INTO "elective_group" VALUES('BSCS-2022:GE-TOTAL','BSCS-2022','GENED','Gen Ed total','min_credits',30,NULL,NULL,0);
INSERT INTO "elective_group" VALUES('BSCS-2022:E01','BSCS-2022','MAJOR-ELEC','Major electives','min_credits',12,NULL,NULL,0);
INSERT INTO "elective_group" VALUES('BSCS-2022:F01','BSCS-2022','FREE-ELEC','Free electives','min_credits',21,NULL,NULL,1);
INSERT INTO "elective_group" VALUES('BSCS-2025:GE-HUM','BSCS-2025','GENED','Humanities and fine arts','min_courses',NULL,1,'humanities',0);
INSERT INTO "elective_group" VALUES('BSCS-2025:GE-SOC','BSCS-2025','GENED','Social and behavioural sciences','min_courses',NULL,1,'social',0);
INSERT INTO "elective_group" VALUES('BSCS-2025:GE-NAT','BSCS-2025','GENED','Natural science and sustainability','min_courses',NULL,1,'natural_math',0);
INSERT INTO "elective_group" VALUES('BSCS-2025:GE-MATH','BSCS-2025','GENED','Mathematics','min_courses',NULL,1,'natural_math',0);
INSERT INTO "elective_group" VALUES('BSCS-2025:GE-TOTAL','BSCS-2025','GENED','Gen Ed total','min_credits',30,NULL,NULL,0);
INSERT INTO "elective_group" VALUES('BSCS-2025:E01','BSCS-2025','MAJOR-ELEC','Major electives','min_credits',12,NULL,NULL,0);
INSERT INTO "elective_group" VALUES('BSCS-2025:F01','BSCS-2025','FREE-ELEC','Free electives','min_credits',18,NULL,NULL,1);
INSERT INTO "elective_group" VALUES('BSCS-2025-AI:GE-HUM','BSCS-2025-AI','GENED','Humanities and fine arts','min_courses',NULL,1,'humanities',0);
INSERT INTO "elective_group" VALUES('BSCS-2025-AI:GE-SOC','BSCS-2025-AI','GENED','Social and behavioural sciences','min_courses',NULL,1,'social',0);
INSERT INTO "elective_group" VALUES('BSCS-2025-AI:GE-NAT','BSCS-2025-AI','GENED','Natural science and sustainability','min_courses',NULL,1,'natural_math',0);
INSERT INTO "elective_group" VALUES('BSCS-2025-AI:GE-MATH','BSCS-2025-AI','GENED','Mathematics','min_courses',NULL,1,'natural_math',0);
INSERT INTO "elective_group" VALUES('BSCS-2025-AI:GE-TOTAL','BSCS-2025-AI','GENED','Gen Ed total','min_credits',30,NULL,NULL,0);
INSERT INTO "elective_group" VALUES('BSCS-2025-AI:E01','BSCS-2025-AI','MAJOR-ELEC','Major electives','min_credits',6,NULL,NULL,0);
INSERT INTO "elective_group" VALUES('BSCS-2025-AI:F01','BSCS-2025-AI','FREE-ELEC','Free electives','min_credits',18,NULL,NULL,1);
INSERT INTO "elective_group" VALUES('BSDS-2022:GE-HUM','BSDS-2022','GENED','Humanities and fine arts','min_courses',NULL,1,'humanities',0);
INSERT INTO "elective_group" VALUES('BSDS-2022:GE-SOC','BSDS-2022','GENED','Social and behavioural sciences','min_courses',NULL,1,'social',0);
INSERT INTO "elective_group" VALUES('BSDS-2022:GE-NAT','BSDS-2022','GENED','Natural science','min_courses',NULL,1,'natural_math',0);
INSERT INTO "elective_group" VALUES('BSDS-2022:GE-MATH','BSDS-2022','GENED','Mathematics','min_courses',NULL,1,'natural_math',0);
INSERT INTO "elective_group" VALUES('BSDS-2022:GE-TOTAL','BSDS-2022','GENED','Gen Ed total','min_credits',30,NULL,NULL,0);
INSERT INTO "elective_group" VALUES('BSDS-2022:E01','BSDS-2022','MAJOR-ELEC','Major electives','min_credits',12,NULL,NULL,0);
INSERT INTO "elective_group" VALUES('BSDS-2022:F01','BSDS-2022','FREE-ELEC','Free electives','min_credits',24,NULL,NULL,1);
INSERT INTO "elective_group" VALUES('BSDS-2025:GE-HUM','BSDS-2025','GENED','Humanities and fine arts','min_courses',NULL,1,'humanities',0);
INSERT INTO "elective_group" VALUES('BSDS-2025:GE-SOC','BSDS-2025','GENED','Social and behavioural sciences','min_courses',NULL,1,'social',0);
INSERT INTO "elective_group" VALUES('BSDS-2025:GE-NAT','BSDS-2025','GENED','Natural science and sustainability','min_courses',NULL,1,'natural_math',0);
INSERT INTO "elective_group" VALUES('BSDS-2025:GE-MATH','BSDS-2025','GENED','Mathematics','min_courses',NULL,1,'natural_math',0);
INSERT INTO "elective_group" VALUES('BSDS-2025:GE-TOTAL','BSDS-2025','GENED','Gen Ed total','min_credits',30,NULL,NULL,0);
INSERT INTO "elective_group" VALUES('BSDS-2025:E01','BSDS-2025','MAJOR-ELEC','Major electives','min_credits',15,NULL,NULL,0);
INSERT INTO "elective_group" VALUES('BSDS-2025:F01','BSDS-2025','FREE-ELEC','Free electives','min_credits',21,NULL,NULL,1);
INSERT INTO "elective_group" VALUES('BSDS-2025-BA:GE-HUM','BSDS-2025-BA','GENED','Humanities and fine arts','min_courses',NULL,1,'humanities',0);
INSERT INTO "elective_group" VALUES('BSDS-2025-BA:GE-SOC','BSDS-2025-BA','GENED','Social and behavioural sciences','min_courses',NULL,1,'social',0);
INSERT INTO "elective_group" VALUES('BSDS-2025-BA:GE-NAT','BSDS-2025-BA','GENED','Natural science and sustainability','min_courses',NULL,1,'natural_math',0);
INSERT INTO "elective_group" VALUES('BSDS-2025-BA:GE-MATH','BSDS-2025-BA','GENED','Mathematics','min_courses',NULL,1,'natural_math',0);
INSERT INTO "elective_group" VALUES('BSDS-2025-BA:GE-TOTAL','BSDS-2025-BA','GENED','Gen Ed total','min_credits',30,NULL,NULL,0);
INSERT INTO "elective_group" VALUES('BSDS-2025-BA:E01','BSDS-2025-BA','MAJOR-ELEC','Major electives','min_credits',9,NULL,NULL,0);
INSERT INTO "elective_group" VALUES('BSDS-2025-BA:F01','BSDS-2025-BA','FREE-ELEC','Free electives','min_credits',21,NULL,NULL,1);
INSERT INTO "elective_group" VALUES('MBUS-2025:E01','MBUS-2025','MINOR-ELEC','Minor electives','min_credits',6,NULL,NULL,0);
INSERT INTO "elective_group" VALUES('MCS-2025:E01','MCS-2025','MINOR-ELEC','Minor electives','min_credits',6,NULL,NULL,0);
INSERT INTO "elective_group" VALUES('MDA-2025:E01','MDA-2025','MINOR-ELEC','Minor electives','min_credits',6,NULL,NULL,0);
CREATE TABLE "elective_group_course" (
  "group_id" TEXT NOT NULL,
  "course_code" TEXT NOT NULL,
  CONSTRAINT "pk_elective_group_course" PRIMARY KEY ("group_id", "course_code")
);
INSERT INTO "elective_group_course" VALUES('BBA-2022:GE-HUM','HUMN110');
INSERT INTO "elective_group_course" VALUES('BBA-2022:GE-HUM','HUMN120');
INSERT INTO "elective_group_course" VALUES('BBA-2022:GE-HUM','ARTS110');
INSERT INTO "elective_group_course" VALUES('BBA-2022:GE-SOC','PSYC101');
INSERT INTO "elective_group_course" VALUES('BBA-2022:GE-SOC','SOCI110');
INSERT INTO "elective_group_course" VALUES('BBA-2022:GE-SOC','ECON101');
INSERT INTO "elective_group_course" VALUES('BBA-2022:GE-NAT','PHYS101');
INSERT INTO "elective_group_course" VALUES('BBA-2022:GE-NAT','BIOL101');
INSERT INTO "elective_group_course" VALUES('BBA-2022:GE-NAT','ENVS101');
INSERT INTO "elective_group_course" VALUES('BBA-2022:GE-MATH','MATH101');
INSERT INTO "elective_group_course" VALUES('BBA-2022:GE-TOTAL','ENGL101');
INSERT INTO "elective_group_course" VALUES('BBA-2022:GE-TOTAL','ENGL102');
INSERT INTO "elective_group_course" VALUES('BBA-2022:GE-TOTAL','UAES101');
INSERT INTO "elective_group_course" VALUES('BBA-2022:GE-TOTAL','INNO201');
INSERT INTO "elective_group_course" VALUES('BBA-2022:GE-TOTAL','HUMN110');
INSERT INTO "elective_group_course" VALUES('BBA-2022:GE-TOTAL','HUMN120');
INSERT INTO "elective_group_course" VALUES('BBA-2022:GE-TOTAL','ARTS110');
INSERT INTO "elective_group_course" VALUES('BBA-2022:GE-TOTAL','PSYC101');
INSERT INTO "elective_group_course" VALUES('BBA-2022:GE-TOTAL','SOCI110');
INSERT INTO "elective_group_course" VALUES('BBA-2022:GE-TOTAL','ECON101');
INSERT INTO "elective_group_course" VALUES('BBA-2022:GE-TOTAL','PHYS101');
INSERT INTO "elective_group_course" VALUES('BBA-2022:GE-TOTAL','PHYS101L');
INSERT INTO "elective_group_course" VALUES('BBA-2022:GE-TOTAL','BIOL101');
INSERT INTO "elective_group_course" VALUES('BBA-2022:GE-TOTAL','ENVS101');
INSERT INTO "elective_group_course" VALUES('BBA-2022:GE-TOTAL','MATH101');
INSERT INTO "elective_group_course" VALUES('BBA-2022:E01','MIS340');
INSERT INTO "elective_group_course" VALUES('BBA-2022:E01','ECON301');
INSERT INTO "elective_group_course" VALUES('BBA-2022:E01','BUSA330');
INSERT INTO "elective_group_course" VALUES('BBA-2022:E01','FIN310');
INSERT INTO "elective_group_course" VALUES('BBA-2022:E01','FIN410');
INSERT INTO "elective_group_course" VALUES('BBA-2022:E01','MKTG410');
INSERT INTO "elective_group_course" VALUES('BBA-2022:E01','MGMT410');
INSERT INTO "elective_group_course" VALUES('BBA-2022:E01','MGMT430');
INSERT INTO "elective_group_course" VALUES('BBA-2025:GE-HUM','HUMN110');
INSERT INTO "elective_group_course" VALUES('BBA-2025:GE-HUM','HUMN120');
INSERT INTO "elective_group_course" VALUES('BBA-2025:GE-HUM','ARTS110');
INSERT INTO "elective_group_course" VALUES('BBA-2025:GE-SOC','PSYC101');
INSERT INTO "elective_group_course" VALUES('BBA-2025:GE-SOC','SOCI110');
INSERT INTO "elective_group_course" VALUES('BBA-2025:GE-SOC','ECON101');
INSERT INTO "elective_group_course" VALUES('BBA-2025:GE-NAT','PHYS101');
INSERT INTO "elective_group_course" VALUES('BBA-2025:GE-NAT','BIOL101');
INSERT INTO "elective_group_course" VALUES('BBA-2025:GE-NAT','ENVS101');
INSERT INTO "elective_group_course" VALUES('BBA-2025:GE-MATH','MATH101');
INSERT INTO "elective_group_course" VALUES('BBA-2025:GE-TOTAL','ENGL101');
INSERT INTO "elective_group_course" VALUES('BBA-2025:GE-TOTAL','ENGL102');
INSERT INTO "elective_group_course" VALUES('BBA-2025:GE-TOTAL','UAES101');
INSERT INTO "elective_group_course" VALUES('BBA-2025:GE-TOTAL','INNO201');
INSERT INTO "elective_group_course" VALUES('BBA-2025:GE-TOTAL','HUMN110');
INSERT INTO "elective_group_course" VALUES('BBA-2025:GE-TOTAL','HUMN120');
INSERT INTO "elective_group_course" VALUES('BBA-2025:GE-TOTAL','ARTS110');
INSERT INTO "elective_group_course" VALUES('BBA-2025:GE-TOTAL','PSYC101');
INSERT INTO "elective_group_course" VALUES('BBA-2025:GE-TOTAL','SOCI110');
INSERT INTO "elective_group_course" VALUES('BBA-2025:GE-TOTAL','ECON101');
INSERT INTO "elective_group_course" VALUES('BBA-2025:GE-TOTAL','PHYS101');
INSERT INTO "elective_group_course" VALUES('BBA-2025:GE-TOTAL','PHYS101L');
INSERT INTO "elective_group_course" VALUES('BBA-2025:GE-TOTAL','BIOL101');
INSERT INTO "elective_group_course" VALUES('BBA-2025:GE-TOTAL','ENVS101');
INSERT INTO "elective_group_course" VALUES('BBA-2025:GE-TOTAL','MATH101');
INSERT INTO "elective_group_course" VALUES('BBA-2025:E01','QM210');
INSERT INTO "elective_group_course" VALUES('BBA-2025:E01','COMP101');
INSERT INTO "elective_group_course" VALUES('BBA-2025:E01','MIS340');
INSERT INTO "elective_group_course" VALUES('BBA-2025:E01','ECON301');
INSERT INTO "elective_group_course" VALUES('BBA-2025:E01','BUSA330');
INSERT INTO "elective_group_course" VALUES('BBA-2025:E01','FIN410');
INSERT INTO "elective_group_course" VALUES('BBA-2025:E01','MKTG410');
INSERT INTO "elective_group_course" VALUES('BBA-2025:E01','MGMT430');
INSERT INTO "elective_group_course" VALUES('BSBAN-2025:GE-HUM','HUMN110');
INSERT INTO "elective_group_course" VALUES('BSBAN-2025:GE-HUM','HUMN120');
INSERT INTO "elective_group_course" VALUES('BSBAN-2025:GE-HUM','ARTS110');
INSERT INTO "elective_group_course" VALUES('BSBAN-2025:GE-SOC','PSYC101');
INSERT INTO "elective_group_course" VALUES('BSBAN-2025:GE-SOC','SOCI110');
INSERT INTO "elective_group_course" VALUES('BSBAN-2025:GE-SOC','ECON101');
INSERT INTO "elective_group_course" VALUES('BSBAN-2025:GE-NAT','PHYS101');
INSERT INTO "elective_group_course" VALUES('BSBAN-2025:GE-NAT','BIOL101');
INSERT INTO "elective_group_course" VALUES('BSBAN-2025:GE-NAT','ENVS101');
INSERT INTO "elective_group_course" VALUES('BSBAN-2025:GE-MATH','MATH101');
INSERT INTO "elective_group_course" VALUES('BSBAN-2025:GE-TOTAL','ENGL101');
INSERT INTO "elective_group_course" VALUES('BSBAN-2025:GE-TOTAL','ENGL102');
INSERT INTO "elective_group_course" VALUES('BSBAN-2025:GE-TOTAL','UAES101');
INSERT INTO "elective_group_course" VALUES('BSBAN-2025:GE-TOTAL','INNO201');
INSERT INTO "elective_group_course" VALUES('BSBAN-2025:GE-TOTAL','HUMN110');
INSERT INTO "elective_group_course" VALUES('BSBAN-2025:GE-TOTAL','HUMN120');
INSERT INTO "elective_group_course" VALUES('BSBAN-2025:GE-TOTAL','ARTS110');
INSERT INTO "elective_group_course" VALUES('BSBAN-2025:GE-TOTAL','PSYC101');
INSERT INTO "elective_group_course" VALUES('BSBAN-2025:GE-TOTAL','SOCI110');
INSERT INTO "elective_group_course" VALUES('BSBAN-2025:GE-TOTAL','ECON101');
INSERT INTO "elective_group_course" VALUES('BSBAN-2025:GE-TOTAL','PHYS101');
INSERT INTO "elective_group_course" VALUES('BSBAN-2025:GE-TOTAL','PHYS101L');
INSERT INTO "elective_group_course" VALUES('BSBAN-2025:GE-TOTAL','BIOL101');
INSERT INTO "elective_group_course" VALUES('BSBAN-2025:GE-TOTAL','ENVS101');
INSERT INTO "elective_group_course" VALUES('BSBAN-2025:GE-TOTAL','MATH101');
INSERT INTO "elective_group_course" VALUES('BSBAN-2025:E01','QM210');
INSERT INTO "elective_group_course" VALUES('BSBAN-2025:E01','COMP201');
INSERT INTO "elective_group_course" VALUES('BSBAN-2025:E01','DSCI410');
INSERT INTO "elective_group_course" VALUES('BSBAN-2025:E01','MKTG201');
INSERT INTO "elective_group_course" VALUES('BSBAN-2025:E01','FIN301');
INSERT INTO "elective_group_course" VALUES('BSBAN-2025:E01','BUSA420');
INSERT INTO "elective_group_course" VALUES('BSBAN-2025:E01','BUSA430');
INSERT INTO "elective_group_course" VALUES('BSBAN-2025:E01','BUSA440');
INSERT INTO "elective_group_course" VALUES('BSCS-2022:GE-HUM','HUMN110');
INSERT INTO "elective_group_course" VALUES('BSCS-2022:GE-HUM','HUMN120');
INSERT INTO "elective_group_course" VALUES('BSCS-2022:GE-HUM','ARTS110');
INSERT INTO "elective_group_course" VALUES('BSCS-2022:GE-SOC','PSYC101');
INSERT INTO "elective_group_course" VALUES('BSCS-2022:GE-SOC','SOCI110');
INSERT INTO "elective_group_course" VALUES('BSCS-2022:GE-SOC','ECON101');
INSERT INTO "elective_group_course" VALUES('BSCS-2022:GE-NAT','PHYS101');
INSERT INTO "elective_group_course" VALUES('BSCS-2022:GE-NAT','BIOL101');
INSERT INTO "elective_group_course" VALUES('BSCS-2022:GE-NAT','ENVS101');
INSERT INTO "elective_group_course" VALUES('BSCS-2022:GE-MATH','MATH101');
INSERT INTO "elective_group_course" VALUES('BSCS-2022:GE-TOTAL','ENGL101');
INSERT INTO "elective_group_course" VALUES('BSCS-2022:GE-TOTAL','ENGL102');
INSERT INTO "elective_group_course" VALUES('BSCS-2022:GE-TOTAL','UAES101');
INSERT INTO "elective_group_course" VALUES('BSCS-2022:GE-TOTAL','INNO201');
INSERT INTO "elective_group_course" VALUES('BSCS-2022:GE-TOTAL','HUMN110');
INSERT INTO "elective_group_course" VALUES('BSCS-2022:GE-TOTAL','HUMN120');
INSERT INTO "elective_group_course" VALUES('BSCS-2022:GE-TOTAL','ARTS110');
INSERT INTO "elective_group_course" VALUES('BSCS-2022:GE-TOTAL','PSYC101');
INSERT INTO "elective_group_course" VALUES('BSCS-2022:GE-TOTAL','SOCI110');
INSERT INTO "elective_group_course" VALUES('BSCS-2022:GE-TOTAL','ECON101');
INSERT INTO "elective_group_course" VALUES('BSCS-2022:GE-TOTAL','PHYS101');
INSERT INTO "elective_group_course" VALUES('BSCS-2022:GE-TOTAL','PHYS101L');
INSERT INTO "elective_group_course" VALUES('BSCS-2022:GE-TOTAL','BIOL101');
INSERT INTO "elective_group_course" VALUES('BSCS-2022:GE-TOTAL','ENVS101');
INSERT INTO "elective_group_course" VALUES('BSCS-2022:GE-TOTAL','MATH101');
INSERT INTO "elective_group_course" VALUES('BSCS-2022:E01','COMP410');
INSERT INTO "elective_group_course" VALUES('BSCS-2022:E01','COMP420');
INSERT INTO "elective_group_course" VALUES('BSCS-2022:E01','COMP430');
INSERT INTO "elective_group_course" VALUES('BSCS-2022:E01','COMP440');
INSERT INTO "elective_group_course" VALUES('BSCS-2022:E01','COMP450');
INSERT INTO "elective_group_course" VALUES('BSCS-2022:E01','COMP460');
INSERT INTO "elective_group_course" VALUES('BSCS-2022:E01','DSCI410');
INSERT INTO "elective_group_course" VALUES('BSCS-2025:GE-HUM','HUMN110');
INSERT INTO "elective_group_course" VALUES('BSCS-2025:GE-HUM','HUMN120');
INSERT INTO "elective_group_course" VALUES('BSCS-2025:GE-HUM','ARTS110');
INSERT INTO "elective_group_course" VALUES('BSCS-2025:GE-SOC','PSYC101');
INSERT INTO "elective_group_course" VALUES('BSCS-2025:GE-SOC','SOCI110');
INSERT INTO "elective_group_course" VALUES('BSCS-2025:GE-SOC','ECON101');
INSERT INTO "elective_group_course" VALUES('BSCS-2025:GE-NAT','PHYS101');
INSERT INTO "elective_group_course" VALUES('BSCS-2025:GE-NAT','BIOL101');
INSERT INTO "elective_group_course" VALUES('BSCS-2025:GE-NAT','ENVS101');
INSERT INTO "elective_group_course" VALUES('BSCS-2025:GE-MATH','MATH101');
INSERT INTO "elective_group_course" VALUES('BSCS-2025:GE-TOTAL','ENGL101');
INSERT INTO "elective_group_course" VALUES('BSCS-2025:GE-TOTAL','ENGL102');
INSERT INTO "elective_group_course" VALUES('BSCS-2025:GE-TOTAL','UAES101');
INSERT INTO "elective_group_course" VALUES('BSCS-2025:GE-TOTAL','INNO201');
INSERT INTO "elective_group_course" VALUES('BSCS-2025:GE-TOTAL','HUMN110');
INSERT INTO "elective_group_course" VALUES('BSCS-2025:GE-TOTAL','HUMN120');
INSERT INTO "elective_group_course" VALUES('BSCS-2025:GE-TOTAL','ARTS110');
INSERT INTO "elective_group_course" VALUES('BSCS-2025:GE-TOTAL','PSYC101');
INSERT INTO "elective_group_course" VALUES('BSCS-2025:GE-TOTAL','SOCI110');
INSERT INTO "elective_group_course" VALUES('BSCS-2025:GE-TOTAL','ECON101');
INSERT INTO "elective_group_course" VALUES('BSCS-2025:GE-TOTAL','PHYS101');
INSERT INTO "elective_group_course" VALUES('BSCS-2025:GE-TOTAL','PHYS101L');
INSERT INTO "elective_group_course" VALUES('BSCS-2025:GE-TOTAL','BIOL101');
INSERT INTO "elective_group_course" VALUES('BSCS-2025:GE-TOTAL','ENVS101');
INSERT INTO "elective_group_course" VALUES('BSCS-2025:GE-TOTAL','MATH101');
INSERT INTO "elective_group_course" VALUES('BSCS-2025:E01','COMP410');
INSERT INTO "elective_group_course" VALUES('BSCS-2025:E01','COMP420');
INSERT INTO "elective_group_course" VALUES('BSCS-2025:E01','COMP430');
INSERT INTO "elective_group_course" VALUES('BSCS-2025:E01','COMP440');
INSERT INTO "elective_group_course" VALUES('BSCS-2025:E01','COMP450');
INSERT INTO "elective_group_course" VALUES('BSCS-2025:E01','COMP460');
INSERT INTO "elective_group_course" VALUES('BSCS-2025:E01','DSCI410');
INSERT INTO "elective_group_course" VALUES('BSCS-2025-AI:GE-HUM','HUMN110');
INSERT INTO "elective_group_course" VALUES('BSCS-2025-AI:GE-HUM','HUMN120');
INSERT INTO "elective_group_course" VALUES('BSCS-2025-AI:GE-HUM','ARTS110');
INSERT INTO "elective_group_course" VALUES('BSCS-2025-AI:GE-SOC','PSYC101');
INSERT INTO "elective_group_course" VALUES('BSCS-2025-AI:GE-SOC','SOCI110');
INSERT INTO "elective_group_course" VALUES('BSCS-2025-AI:GE-SOC','ECON101');
INSERT INTO "elective_group_course" VALUES('BSCS-2025-AI:GE-NAT','PHYS101');
INSERT INTO "elective_group_course" VALUES('BSCS-2025-AI:GE-NAT','BIOL101');
INSERT INTO "elective_group_course" VALUES('BSCS-2025-AI:GE-NAT','ENVS101');
INSERT INTO "elective_group_course" VALUES('BSCS-2025-AI:GE-MATH','MATH101');
INSERT INTO "elective_group_course" VALUES('BSCS-2025-AI:GE-TOTAL','ENGL101');
INSERT INTO "elective_group_course" VALUES('BSCS-2025-AI:GE-TOTAL','ENGL102');
INSERT INTO "elective_group_course" VALUES('BSCS-2025-AI:GE-TOTAL','UAES101');
INSERT INTO "elective_group_course" VALUES('BSCS-2025-AI:GE-TOTAL','INNO201');
INSERT INTO "elective_group_course" VALUES('BSCS-2025-AI:GE-TOTAL','HUMN110');
INSERT INTO "elective_group_course" VALUES('BSCS-2025-AI:GE-TOTAL','HUMN120');
INSERT INTO "elective_group_course" VALUES('BSCS-2025-AI:GE-TOTAL','ARTS110');
INSERT INTO "elective_group_course" VALUES('BSCS-2025-AI:GE-TOTAL','PSYC101');
INSERT INTO "elective_group_course" VALUES('BSCS-2025-AI:GE-TOTAL','SOCI110');
INSERT INTO "elective_group_course" VALUES('BSCS-2025-AI:GE-TOTAL','ECON101');
INSERT INTO "elective_group_course" VALUES('BSCS-2025-AI:GE-TOTAL','PHYS101');
INSERT INTO "elective_group_course" VALUES('BSCS-2025-AI:GE-TOTAL','PHYS101L');
INSERT INTO "elective_group_course" VALUES('BSCS-2025-AI:GE-TOTAL','BIOL101');
INSERT INTO "elective_group_course" VALUES('BSCS-2025-AI:GE-TOTAL','ENVS101');
INSERT INTO "elective_group_course" VALUES('BSCS-2025-AI:GE-TOTAL','MATH101');
INSERT INTO "elective_group_course" VALUES('BSCS-2025-AI:E01','COMP410');
INSERT INTO "elective_group_course" VALUES('BSCS-2025-AI:E01','COMP420');
INSERT INTO "elective_group_course" VALUES('BSCS-2025-AI:E01','COMP440');
INSERT INTO "elective_group_course" VALUES('BSCS-2025-AI:E01','COMP450');
INSERT INTO "elective_group_course" VALUES('BSCS-2025-AI:E01','COMP460');
INSERT INTO "elective_group_course" VALUES('BSCS-2025-AI:E01','DSCI430');
INSERT INTO "elective_group_course" VALUES('BSDS-2022:GE-HUM','HUMN110');
INSERT INTO "elective_group_course" VALUES('BSDS-2022:GE-HUM','HUMN120');
INSERT INTO "elective_group_course" VALUES('BSDS-2022:GE-HUM','ARTS110');
INSERT INTO "elective_group_course" VALUES('BSDS-2022:GE-SOC','PSYC101');
INSERT INTO "elective_group_course" VALUES('BSDS-2022:GE-SOC','SOCI110');
INSERT INTO "elective_group_course" VALUES('BSDS-2022:GE-SOC','ECON101');
INSERT INTO "elective_group_course" VALUES('BSDS-2022:GE-NAT','PHYS101');
INSERT INTO "elective_group_course" VALUES('BSDS-2022:GE-NAT','BIOL101');
INSERT INTO "elective_group_course" VALUES('BSDS-2022:GE-NAT','ENVS101');
INSERT INTO "elective_group_course" VALUES('BSDS-2022:GE-MATH','MATH101');
INSERT INTO "elective_group_course" VALUES('BSDS-2022:GE-TOTAL','ENGL101');
INSERT INTO "elective_group_course" VALUES('BSDS-2022:GE-TOTAL','ENGL102');
INSERT INTO "elective_group_course" VALUES('BSDS-2022:GE-TOTAL','UAES101');
INSERT INTO "elective_group_course" VALUES('BSDS-2022:GE-TOTAL','INNO201');
INSERT INTO "elective_group_course" VALUES('BSDS-2022:GE-TOTAL','HUMN110');
INSERT INTO "elective_group_course" VALUES('BSDS-2022:GE-TOTAL','HUMN120');
INSERT INTO "elective_group_course" VALUES('BSDS-2022:GE-TOTAL','ARTS110');
INSERT INTO "elective_group_course" VALUES('BSDS-2022:GE-TOTAL','PSYC101');
INSERT INTO "elective_group_course" VALUES('BSDS-2022:GE-TOTAL','SOCI110');
INSERT INTO "elective_group_course" VALUES('BSDS-2022:GE-TOTAL','ECON101');
INSERT INTO "elective_group_course" VALUES('BSDS-2022:GE-TOTAL','PHYS101');
INSERT INTO "elective_group_course" VALUES('BSDS-2022:GE-TOTAL','PHYS101L');
INSERT INTO "elective_group_course" VALUES('BSDS-2022:GE-TOTAL','BIOL101');
INSERT INTO "elective_group_course" VALUES('BSDS-2022:GE-TOTAL','ENVS101');
INSERT INTO "elective_group_course" VALUES('BSDS-2022:GE-TOTAL','MATH101');
INSERT INTO "elective_group_course" VALUES('BSDS-2022:E01','COMP301');
INSERT INTO "elective_group_course" VALUES('BSDS-2022:E01','COMP410');
INSERT INTO "elective_group_course" VALUES('BSDS-2022:E01','DSCI430');
INSERT INTO "elective_group_course" VALUES('BSDS-2022:E01','DSCI440');
INSERT INTO "elective_group_course" VALUES('BSDS-2022:E01','DSCI450');
INSERT INTO "elective_group_course" VALUES('BSDS-2022:E01','DSCI460');
INSERT INTO "elective_group_course" VALUES('BSDS-2025:GE-HUM','HUMN110');
INSERT INTO "elective_group_course" VALUES('BSDS-2025:GE-HUM','HUMN120');
INSERT INTO "elective_group_course" VALUES('BSDS-2025:GE-HUM','ARTS110');
INSERT INTO "elective_group_course" VALUES('BSDS-2025:GE-SOC','PSYC101');
INSERT INTO "elective_group_course" VALUES('BSDS-2025:GE-SOC','SOCI110');
INSERT INTO "elective_group_course" VALUES('BSDS-2025:GE-SOC','ECON101');
INSERT INTO "elective_group_course" VALUES('BSDS-2025:GE-NAT','PHYS101');
INSERT INTO "elective_group_course" VALUES('BSDS-2025:GE-NAT','BIOL101');
INSERT INTO "elective_group_course" VALUES('BSDS-2025:GE-NAT','ENVS101');
INSERT INTO "elective_group_course" VALUES('BSDS-2025:GE-MATH','MATH101');
INSERT INTO "elective_group_course" VALUES('BSDS-2025:GE-TOTAL','ENGL101');
INSERT INTO "elective_group_course" VALUES('BSDS-2025:GE-TOTAL','ENGL102');
INSERT INTO "elective_group_course" VALUES('BSDS-2025:GE-TOTAL','UAES101');
INSERT INTO "elective_group_course" VALUES('BSDS-2025:GE-TOTAL','INNO201');
INSERT INTO "elective_group_course" VALUES('BSDS-2025:GE-TOTAL','HUMN110');
INSERT INTO "elective_group_course" VALUES('BSDS-2025:GE-TOTAL','HUMN120');
INSERT INTO "elective_group_course" VALUES('BSDS-2025:GE-TOTAL','ARTS110');
INSERT INTO "elective_group_course" VALUES('BSDS-2025:GE-TOTAL','PSYC101');
INSERT INTO "elective_group_course" VALUES('BSDS-2025:GE-TOTAL','SOCI110');
INSERT INTO "elective_group_course" VALUES('BSDS-2025:GE-TOTAL','ECON101');
INSERT INTO "elective_group_course" VALUES('BSDS-2025:GE-TOTAL','PHYS101');
INSERT INTO "elective_group_course" VALUES('BSDS-2025:GE-TOTAL','PHYS101L');
INSERT INTO "elective_group_course" VALUES('BSDS-2025:GE-TOTAL','BIOL101');
INSERT INTO "elective_group_course" VALUES('BSDS-2025:GE-TOTAL','ENVS101');
INSERT INTO "elective_group_course" VALUES('BSDS-2025:GE-TOTAL','MATH101');
INSERT INTO "elective_group_course" VALUES('BSDS-2025:E01','COMP301');
INSERT INTO "elective_group_course" VALUES('BSDS-2025:E01','COMP410');
INSERT INTO "elective_group_course" VALUES('BSDS-2025:E01','COMP420');
INSERT INTO "elective_group_course" VALUES('BSDS-2025:E01','DSCI430');
INSERT INTO "elective_group_course" VALUES('BSDS-2025:E01','DSCI440');
INSERT INTO "elective_group_course" VALUES('BSDS-2025:E01','DSCI450');
INSERT INTO "elective_group_course" VALUES('BSDS-2025:E01','DSCI460');
INSERT INTO "elective_group_course" VALUES('BSDS-2025-BA:GE-HUM','HUMN110');
INSERT INTO "elective_group_course" VALUES('BSDS-2025-BA:GE-HUM','HUMN120');
INSERT INTO "elective_group_course" VALUES('BSDS-2025-BA:GE-HUM','ARTS110');
INSERT INTO "elective_group_course" VALUES('BSDS-2025-BA:GE-SOC','PSYC101');
INSERT INTO "elective_group_course" VALUES('BSDS-2025-BA:GE-SOC','SOCI110');
INSERT INTO "elective_group_course" VALUES('BSDS-2025-BA:GE-SOC','ECON101');
INSERT INTO "elective_group_course" VALUES('BSDS-2025-BA:GE-NAT','PHYS101');
INSERT INTO "elective_group_course" VALUES('BSDS-2025-BA:GE-NAT','BIOL101');
INSERT INTO "elective_group_course" VALUES('BSDS-2025-BA:GE-NAT','ENVS101');
INSERT INTO "elective_group_course" VALUES('BSDS-2025-BA:GE-MATH','MATH101');
INSERT INTO "elective_group_course" VALUES('BSDS-2025-BA:GE-TOTAL','ENGL101');
INSERT INTO "elective_group_course" VALUES('BSDS-2025-BA:GE-TOTAL','ENGL102');
INSERT INTO "elective_group_course" VALUES('BSDS-2025-BA:GE-TOTAL','UAES101');
INSERT INTO "elective_group_course" VALUES('BSDS-2025-BA:GE-TOTAL','INNO201');
INSERT INTO "elective_group_course" VALUES('BSDS-2025-BA:GE-TOTAL','HUMN110');
INSERT INTO "elective_group_course" VALUES('BSDS-2025-BA:GE-TOTAL','HUMN120');
INSERT INTO "elective_group_course" VALUES('BSDS-2025-BA:GE-TOTAL','ARTS110');
INSERT INTO "elective_group_course" VALUES('BSDS-2025-BA:GE-TOTAL','PSYC101');
INSERT INTO "elective_group_course" VALUES('BSDS-2025-BA:GE-TOTAL','SOCI110');
INSERT INTO "elective_group_course" VALUES('BSDS-2025-BA:GE-TOTAL','ECON101');
INSERT INTO "elective_group_course" VALUES('BSDS-2025-BA:GE-TOTAL','PHYS101');
INSERT INTO "elective_group_course" VALUES('BSDS-2025-BA:GE-TOTAL','PHYS101L');
INSERT INTO "elective_group_course" VALUES('BSDS-2025-BA:GE-TOTAL','BIOL101');
INSERT INTO "elective_group_course" VALUES('BSDS-2025-BA:GE-TOTAL','ENVS101');
INSERT INTO "elective_group_course" VALUES('BSDS-2025-BA:GE-TOTAL','MATH101');
INSERT INTO "elective_group_course" VALUES('BSDS-2025-BA:E01','COMP301');
INSERT INTO "elective_group_course" VALUES('BSDS-2025-BA:E01','COMP410');
INSERT INTO "elective_group_course" VALUES('BSDS-2025-BA:E01','COMP420');
INSERT INTO "elective_group_course" VALUES('BSDS-2025-BA:E01','DSCI430');
INSERT INTO "elective_group_course" VALUES('BSDS-2025-BA:E01','DSCI440');
INSERT INTO "elective_group_course" VALUES('BSDS-2025-BA:E01','DSCI450');
INSERT INTO "elective_group_course" VALUES('BSDS-2025-BA:E01','DSCI460');
INSERT INTO "elective_group_course" VALUES('BSDS-2025-BA:E01','BUSA430');
INSERT INTO "elective_group_course" VALUES('BSDS-2025-BA:E01','BUSA440');
INSERT INTO "elective_group_course" VALUES('MBUS-2025:E01','MIS340');
INSERT INTO "elective_group_course" VALUES('MBUS-2025:E01','MKTG201');
INSERT INTO "elective_group_course" VALUES('MBUS-2025:E01','FIN301');
INSERT INTO "elective_group_course" VALUES('MCS-2025:E01','COMP301');
INSERT INTO "elective_group_course" VALUES('MCS-2025:E01','COMP340');
INSERT INTO "elective_group_course" VALUES('MCS-2025:E01','COMP410');
INSERT INTO "elective_group_course" VALUES('MDA-2025:E01','DSCI210');
INSERT INTO "elective_group_course" VALUES('MDA-2025:E01','DSCI320');
INSERT INTO "elective_group_course" VALUES('MDA-2025:E01','DSCI410');
INSERT INTO "elective_group_course" VALUES('MDA-2025:E01','DSCI450');
INSERT INTO "elective_group_course" VALUES('MDA-2025:E01','BUSA330');
CREATE TABLE "enrollment" (
  "enrollment_id" INTEGER NOT NULL,
  "student_id" TEXT NOT NULL,
  "term_code" TEXT NOT NULL,
  "course_code" TEXT NOT NULL,
  "section" TEXT NOT NULL,
  "grade" TEXT,
  "credits" INTEGER,
  "origin" TEXT NOT NULL,
  CONSTRAINT "pk_enrollment" PRIMARY KEY ("enrollment_id")
);
INSERT INTO "enrollment" VALUES(1,'S20221356','2022FA','ENGL101','01','B',3,'institutional');
INSERT INTO "enrollment" VALUES(2,'S20221356','2022FA','MATH101','01','B+',3,'institutional');
INSERT INTO "enrollment" VALUES(3,'S20221356','2022FA','CSC101','01','A-',3,'institutional');
INSERT INTO "enrollment" VALUES(4,'S20221356','2022FA','UAES101','01','B+',3,'institutional');
INSERT INTO "enrollment" VALUES(5,'S20221356','2022FA','HUMN110','01','B',3,'institutional');
INSERT INTO "enrollment" VALUES(6,'S20221356','2023SP','ENGL102','01','B-',3,'institutional');
INSERT INTO "enrollment" VALUES(7,'S20221356','2023SP','MATH102','01','B',3,'institutional');
INSERT INTO "enrollment" VALUES(8,'S20221356','2023SP','CSC201','01','B+',3,'institutional');
INSERT INTO "enrollment" VALUES(9,'S20221356','2023SP','PSYC101','01','B',3,'institutional');
INSERT INTO "enrollment" VALUES(10,'S20221356','2023SP','BIOL101','01','C+',3,'institutional');
INSERT INTO "enrollment" VALUES(11,'S20221356','2023SU','ARTS110','01','B+',3,'institutional');
INSERT INTO "enrollment" VALUES(12,'S20221356','2023FA','MATH210','01','B',3,'institutional');
INSERT INTO "enrollment" VALUES(13,'S20221356','2023FA','STAT201','01','B-',3,'institutional');
INSERT INTO "enrollment" VALUES(14,'S20221356','2023FA','COMP210','01','B',3,'institutional');
INSERT INTO "enrollment" VALUES(15,'S20221356','2023FA','COMP220','01','C+',3,'institutional');
INSERT INTO "enrollment" VALUES(16,'S20221356','2023FA','INNO201','01','A-',3,'institutional');
INSERT INTO "enrollment" VALUES(17,'S20221356','2024SP','COMP230','01','B',3,'institutional');
INSERT INTO "enrollment" VALUES(18,'S20221356','2024SP','COMP250','01','C+',4,'institutional');
INSERT INTO "enrollment" VALUES(19,'S20221356','2024SP','COMP301','01','B',3,'institutional');
INSERT INTO "enrollment" VALUES(20,'S20221356','2024SP','COMP340','01','B-',3,'institutional');
INSERT INTO "enrollment" VALUES(21,'S20221356','2024SP','SOCI110','01','A-',3,'institutional');
INSERT INTO "enrollment" VALUES(22,'S20221356','2024SU','ENVS101','01','B',3,'institutional');
INSERT INTO "enrollment" VALUES(23,'S20221356','2024FA','COMP320','01','B',3,'institutional');
INSERT INTO "enrollment" VALUES(24,'S20221356','2024FA','COMP310','01','C+',3,'institutional');
INSERT INTO "enrollment" VALUES(25,'S20221356','2024FA','COMP400','01','B+',3,'institutional');
INSERT INTO "enrollment" VALUES(26,'S20221356','2024FA','COMP395','01','A',2,'institutional');
INSERT INTO "enrollment" VALUES(27,'S20221356','2024FA','MGMT201','01','B',3,'institutional');
INSERT INTO "enrollment" VALUES(28,'S20221356','2025SP','COMP315','01','B-',3,'institutional');
INSERT INTO "enrollment" VALUES(29,'S20221356','2025SP','COMP410','01','B',3,'institutional');
INSERT INTO "enrollment" VALUES(30,'S20221356','2025SP','COMP420','01','B+',3,'institutional');
INSERT INTO "enrollment" VALUES(31,'S20221356','2025SP','ACCT201','01','B',3,'institutional');
INSERT INTO "enrollment" VALUES(32,'S20221356','2025SP','MKTG201','01','C+',3,'institutional');
INSERT INTO "enrollment" VALUES(33,'S20221356','2025SP','ECON101','01','B',3,'institutional');
INSERT INTO "enrollment" VALUES(34,'S20221356','2025SU','INTR390','01','B+',6,'institutional');
INSERT INTO "enrollment" VALUES(35,'S20221356','2025FA','COMP440','01','B',3,'institutional');
INSERT INTO "enrollment" VALUES(36,'S20221356','2025FA','COMP450','01','B-',3,'institutional');
INSERT INTO "enrollment" VALUES(37,'S20221356','2025FA','DSCI220','01','B',3,'institutional');
INSERT INTO "enrollment" VALUES(38,'S20221356','2025FA','BUSL210','01','B',3,'institutional');
INSERT INTO "enrollment" VALUES(39,'S20221356','2026SP','DSCI395','01','A-',2,'institutional');
INSERT INTO "enrollment" VALUES(40,'S20222001','2022FA','ENGL101','01','B',3,'institutional');
INSERT INTO "enrollment" VALUES(41,'S20222001','2022FA','MATH101','01','B+',3,'institutional');
INSERT INTO "enrollment" VALUES(42,'S20222001','2022FA','CSC101','01','A-',3,'institutional');
INSERT INTO "enrollment" VALUES(43,'S20222001','2022FA','UAES101','01','A',3,'institutional');
INSERT INTO "enrollment" VALUES(44,'S20222001','2022FA','HUMN110','01','B',3,'institutional');
INSERT INTO "enrollment" VALUES(45,'S20222001','2023SP','ENGL102','01','B+',3,'institutional');
INSERT INTO "enrollment" VALUES(46,'S20222001','2023SP','MATH102','01','B',3,'institutional');
INSERT INTO "enrollment" VALUES(47,'S20222001','2023SP','CSC201','01','B',3,'institutional');
INSERT INTO "enrollment" VALUES(48,'S20222001','2023SP','PSYC101','01','A-',3,'institutional');
INSERT INTO "enrollment" VALUES(49,'S20222001','2023SP','BIOL101','01','B',3,'institutional');
INSERT INTO "enrollment" VALUES(50,'S20222001','2023FA','MATH210','01','B-',3,'institutional');
INSERT INTO "enrollment" VALUES(51,'S20222001','2023FA','STAT201','01','B',3,'institutional');
INSERT INTO "enrollment" VALUES(52,'S20222001','2023FA','COMP210','01','B+',3,'institutional');
INSERT INTO "enrollment" VALUES(53,'S20222001','2023FA','COMP220','01','B',3,'institutional');
INSERT INTO "enrollment" VALUES(54,'S20222001','2023FA','INNO201','01','A-',3,'institutional');
INSERT INTO "enrollment" VALUES(55,'S20222001','2024SP','COMP230','01','B',3,'institutional');
INSERT INTO "enrollment" VALUES(56,'S20222001','2024SP','COMP250','01','B-',4,'institutional');
INSERT INTO "enrollment" VALUES(57,'S20222001','2024SP','COMP301','01','B',3,'institutional');
INSERT INTO "enrollment" VALUES(58,'S20222001','2024SP','COMP340','01','B+',3,'institutional');
INSERT INTO "enrollment" VALUES(59,'S20222001','2024SP','ECON101','01','B',3,'institutional');
INSERT INTO "enrollment" VALUES(60,'S20222001','2024SU','HUMN120','01','A',3,'institutional');
INSERT INTO "enrollment" VALUES(61,'S20222001','2024FA','COMP320','01','B',3,'institutional');
INSERT INTO "enrollment" VALUES(62,'S20222001','2024FA','COMP310','01','C+',3,'institutional');
INSERT INTO "enrollment" VALUES(63,'S20222001','2024FA','COMP315','01','B',3,'institutional');
INSERT INTO "enrollment" VALUES(64,'S20222001','2024FA','COMP395','01','A',2,'institutional');
INSERT INTO "enrollment" VALUES(65,'S20222001','2024FA','COMP410','01','B+',3,'institutional');
INSERT INTO "enrollment" VALUES(66,'S20222001','2024FA','ENVS101','01','B',3,'institutional');
INSERT INTO "enrollment" VALUES(67,'S20222001','2025SP','COMP400','01','B+',3,'institutional');
INSERT INTO "enrollment" VALUES(68,'S20222001','2025SP','COMP420','01','B',3,'institutional');
INSERT INTO "enrollment" VALUES(69,'S20222001','2025SP','MGMT201','01','B',3,'institutional');
INSERT INTO "enrollment" VALUES(70,'S20222001','2025SP','ACCT201','01','B-',3,'institutional');
INSERT INTO "enrollment" VALUES(71,'S20222001','2025SP','MKTG201','01','B',3,'institutional');
INSERT INTO "enrollment" VALUES(72,'S20222001','2025SU','INTR390','01','A',6,'institutional');
INSERT INTO "enrollment" VALUES(73,'S20222001','2025FA','COMP430','01','B',3,'institutional');
INSERT INTO "enrollment" VALUES(74,'S20222001','2025FA','COMP440','01','B+',3,'institutional');
INSERT INTO "enrollment" VALUES(75,'S20222001','2025FA','BUSL210','01','B',3,'institutional');
INSERT INTO "enrollment" VALUES(76,'S20222001','2025FA','DSCI220','01','B-',3,'institutional');
INSERT INTO "enrollment" VALUES(77,'S20222001','2026SP','ECON102','01','B',3,'institutional');
INSERT INTO "enrollment" VALUES(78,'S20222001','2026SP','DSCI230','01','B',3,'institutional');
INSERT INTO "enrollment" VALUES(79,'S20231047','2023FA','ENGL101','01','B',3,'institutional');
INSERT INTO "enrollment" VALUES(80,'S20231047','2023FA','MATH101','01','B+',3,'institutional');
INSERT INTO "enrollment" VALUES(81,'S20231047','2023FA','CSC101','01','A-',3,'institutional');
INSERT INTO "enrollment" VALUES(82,'S20231047','2023FA','UAES101','01','A',3,'institutional');
INSERT INTO "enrollment" VALUES(83,'S20231047','2023FA','HUMN110','01','B',3,'institutional');
INSERT INTO "enrollment" VALUES(84,'S20231047','2023FA','BIOL101','01','B-',3,'institutional');
INSERT INTO "enrollment" VALUES(85,'S20231047','2024SP','ENGL102','01','B',3,'institutional');
INSERT INTO "enrollment" VALUES(86,'S20231047','2024SP','MATH102','01','C+',3,'institutional');
INSERT INTO "enrollment" VALUES(87,'S20231047','2024SP','CSC201','01','B',3,'institutional');
INSERT INTO "enrollment" VALUES(88,'S20231047','2024SP','PSYC101','01','B+',3,'institutional');
INSERT INTO "enrollment" VALUES(89,'S20231047','2024SP','HUMN120','01','A-',3,'institutional');
INSERT INTO "enrollment" VALUES(90,'S20231047','2024SP','ECON101','01','B',3,'institutional');
INSERT INTO "enrollment" VALUES(91,'S20231047','2024SU','MATH210','01','B',3,'institutional');
INSERT INTO "enrollment" VALUES(92,'S20231047','2024SU','INNO201','01','A',3,'institutional');
INSERT INTO "enrollment" VALUES(93,'S20231047','2024FA','STAT201','01','B-',3,'institutional');
INSERT INTO "enrollment" VALUES(94,'S20231047','2024FA','COMP210','01','B+',3,'institutional');
INSERT INTO "enrollment" VALUES(95,'S20231047','2024FA','COMP220','01','B',3,'institutional');
INSERT INTO "enrollment" VALUES(96,'S20231047','2024FA','COMP250','01','C+',4,'institutional');
INSERT INTO "enrollment" VALUES(97,'S20231047','2024FA','COMP301','01','C+',3,'institutional');
INSERT INTO "enrollment" VALUES(98,'S20231047','2024FA','COMP340','01','B',3,'institutional');
INSERT INTO "enrollment" VALUES(99,'S20231047','2025SP','COMP230','01','B',3,'institutional');
INSERT INTO "enrollment" VALUES(100,'S20231047','2025SP','COMP310','01','B-',3,'institutional');
INSERT INTO "enrollment" VALUES(101,'S20231047','2025SP','COMP315','01','C',3,'institutional');
INSERT INTO "enrollment" VALUES(102,'S20231047','2025SP','COMP395','01','A',2,'institutional');
INSERT INTO "enrollment" VALUES(103,'S20231047','2025SP','COMP400','01','B+',3,'institutional');
INSERT INTO "enrollment" VALUES(104,'S20231047','2025SP','ARTS110','01','A',3,'institutional');
INSERT INTO "enrollment" VALUES(105,'S20231047','2025SU','PHYS101','01','C',3,'institutional');
INSERT INTO "enrollment" VALUES(106,'S20231047','2025SU','PHYS101L','01','B',1,'institutional');
INSERT INTO "enrollment" VALUES(107,'S20231047','2025SU','MGMT201','01','B',3,'institutional');
INSERT INTO "enrollment" VALUES(108,'S20231047','2025FA','COMP320','01','B',3,'institutional');
INSERT INTO "enrollment" VALUES(109,'S20231047','2025FA','COMP410','01','B',3,'institutional');
INSERT INTO "enrollment" VALUES(110,'S20231047','2025FA','COMP420','01','C+',3,'institutional');
INSERT INTO "enrollment" VALUES(111,'S20231047','2025FA','ACCT201','01','B-',3,'institutional');
INSERT INTO "enrollment" VALUES(112,'S20231047','2025FA','MKTG201','01','B',3,'institutional');
INSERT INTO "enrollment" VALUES(113,'S20231047','2025FA','ENVS101','01','B+',3,'institutional');
INSERT INTO "enrollment" VALUES(114,'S20231047','2026SP','COMP430','01','B+',3,'institutional');
INSERT INTO "enrollment" VALUES(115,'S20231047','2026SP','SOCI110','01','A-',3,'institutional');
INSERT INTO "enrollment" VALUES(116,'S20231047','2026SP','DSCI230','01','B',3,'institutional');
INSERT INTO "enrollment" VALUES(117,'S20231047','2026SP','BUSL210','01','B',3,'institutional');
INSERT INTO "enrollment" VALUES(118,'S20231047','2026SP','INTR390','01','A',6,'institutional');
INSERT INTO "enrollment" VALUES(119,'S29990001','2022FA','ENGL101','01','B',3,'institutional');
INSERT INTO "enrollment" VALUES(120,'S29990002','2022FA','ENGL101','01','B',3,'institutional');
CREATE TABLE "exception" (
  "exception_id" TEXT NOT NULL,
  "student_id" TEXT NOT NULL,
  "kind" TEXT NOT NULL,
  "course_code" TEXT NOT NULL,
  "replacement_code" TEXT,
  "approval_ref" TEXT NOT NULL,
  "status" TEXT NOT NULL,
  "effective_date" TEXT,
  "approver" TEXT,
  "waiver_type" TEXT,
  "policy_clause" TEXT,
  CONSTRAINT "pk_exception" PRIMARY KEY ("exception_id")
);
CREATE TABLE "grade_code" (
  "grade" TEXT NOT NULL,
  "meaning" TEXT NOT NULL,
  "earned" INTEGER NOT NULL,
  "attempted" INTEGER NOT NULL,
  "in_gpa" INTEGER NOT NULL,
  "rank" INTEGER,
  CONSTRAINT "pk_grade_code" PRIMARY KEY ("grade")
);
INSERT INTO "grade_code" VALUES('A','Excellent',1,1,1,9);
INSERT INTO "grade_code" VALUES('A-','Excellent',1,1,1,8);
INSERT INTO "grade_code" VALUES('B+','Very Good',1,1,1,7);
INSERT INTO "grade_code" VALUES('B','Very Good',1,1,1,6);
INSERT INTO "grade_code" VALUES('B-','Good',1,1,1,5);
INSERT INTO "grade_code" VALUES('C+','Satisfactory',1,1,1,4);
INSERT INTO "grade_code" VALUES('C','Satisfactory',1,1,1,3);
INSERT INTO "grade_code" VALUES('D+','Minimum Pass',1,1,1,2);
INSERT INTO "grade_code" VALUES('D','Minimum Pass',1,1,1,1);
INSERT INTO "grade_code" VALUES('F','Fail',0,1,1,0);
INSERT INTO "grade_code" VALUES('FA','Fail due to attendance',0,1,1,0);
INSERT INTO "grade_code" VALUES('XF','Administrative / disciplinary fail',0,1,1,0);
INSERT INTO "grade_code" VALUES('WF','Withdraw fail',0,1,1,0);
INSERT INTO "grade_code" VALUES('P','Pass',1,1,0,NULL);
INSERT INTO "grade_code" VALUES('NP','No Pass',0,1,0,NULL);
INSERT INTO "grade_code" VALUES('TR','Transfer credit',1,0,0,NULL);
INSERT INTO "grade_code" VALUES('EX','Exempted / credit by examination',1,0,0,NULL);
INSERT INTO "grade_code" VALUES('W','Withdraw',0,0,0,NULL);
INSERT INTO "grade_code" VALUES('I','Incomplete',0,1,0,NULL);
INSERT INTO "grade_code" VALUES('IP','In progress',0,0,0,NULL);
INSERT INTO "grade_code" VALUES('NR','Not reported',0,1,0,NULL);
INSERT INTO "grade_code" VALUES('NULL','Missing grade',0,1,0,NULL);
CREATE TABLE "program" (
  "program_code" TEXT NOT NULL,
  "name" TEXT NOT NULL,
  "kind" TEXT NOT NULL,
  CONSTRAINT "pk_program" PRIMARY KEY ("program_code")
);
INSERT INTO "program" VALUES('BSCS','BS Computer Science','major');
INSERT INTO "program" VALUES('BSDS','BS Data Science','major');
INSERT INTO "program" VALUES('BBA','Bachelor of Business Administration','major');
INSERT INTO "program" VALUES('BSBAN','BS Business Analytics','major');
INSERT INTO "program" VALUES('MDA','Minor in Data Analytics','minor');
INSERT INTO "program" VALUES('MCS','Minor in Computer Science','minor');
INSERT INTO "program" VALUES('MBUS','Minor in Business','minor');
CREATE TABLE "student" (
  "student_id" TEXT NOT NULL,
  "name" TEXT NOT NULL,
  "admit_term" TEXT NOT NULL,
  "first_enrolled_term" TEXT NOT NULL,
  "expected_grad_term" TEXT NOT NULL,
  "standing" TEXT NOT NULL,
  CONSTRAINT "pk_student" PRIMARY KEY ("student_id")
);
INSERT INTO "student" VALUES('S20221356','Synthetic Student 1356','2022FA','2022FA','2026SP','good');
INSERT INTO "student" VALUES('S20222001','Synthetic Student 2001','2022FA','2022FA','2026SP','good');
INSERT INTO "student" VALUES('S20231047','Synthetic Student 1047','2023FA','2023FA','2026SP','good');
INSERT INTO "student" VALUES('S29990001','Synthetic Student 0001','2022FA','2022FA','2026SP','good');
INSERT INTO "student" VALUES('S29990002','Synthetic Student 0002','2022FA','2022FA','2026SP','good');
CREATE TABLE "student_academic" (
  "student_id" TEXT NOT NULL,
  "program_code" TEXT NOT NULL,
  "version_id" TEXT NOT NULL,
  "catalog_year" INTEGER,
  "cgpa" REAL,
  "earned_credits" INTEGER,
  "graduation_status" TEXT,
  CONSTRAINT "pk_student_academic" PRIMARY KEY ("student_id")
);
INSERT INTO "student_academic" VALUES('S20221356','BSCS','BSCS-2022',2022,3.08,120,'eligible (pending)');
INSERT INTO "student_academic" VALUES('S20222001','BSCS','BSCS-2022',2022,3.17,120,'eligible (pending)');
INSERT INTO "student_academic" VALUES('S20231047','BSCS','BSCS-2022',2022,3.05,121,'not eligible');
CREATE TABLE "student_study_plan" (
  "student_id" TEXT NOT NULL,
  "version_id" TEXT NOT NULL,
  "program_code" TEXT NOT NULL,
  "kind" TEXT NOT NULL,
  "from_term" TEXT NOT NULL,
  "to_term" TEXT,
  "is_current" INTEGER NOT NULL,
  "reason" TEXT NOT NULL,
  "approval_ref" TEXT,
  "catalog_year" INTEGER,
  CONSTRAINT "pk_student_study_plan" PRIMARY KEY ("student_id", "version_id", "from_term")
);
INSERT INTO "student_study_plan" VALUES('S20221356','BSCS-2022','BSCS','major','2022FA',NULL,1,'admission',NULL,2022);
INSERT INTO "student_study_plan" VALUES('S20222001','BSCS-2022','BSCS','major','2022FA',NULL,1,'admission',NULL,2022);
INSERT INTO "student_study_plan" VALUES('S20231047','BSCS-2022','BSCS','major','2023FA',NULL,1,'admission',NULL,2022);
INSERT INTO "student_study_plan" VALUES('S29990002','BSCS-2022','BSCS','major','2022FA',NULL,1,'admission',NULL,2022);
INSERT INTO "student_study_plan" VALUES('S29990002','BSCS-2025','BSCS','major','2025FA',NULL,1,'migration','SA-2025-04',2025);
CREATE TABLE "study_plan_course_map" (
  "map_id" TEXT NOT NULL,
  "from_version" TEXT NOT NULL,
  "to_version" TEXT NOT NULL,
  "kind" TEXT NOT NULL,
  "from_codes" TEXT NOT NULL,
  "to_codes" TEXT NOT NULL,
  "relation_id" TEXT,
  "needs_substitution" INTEGER NOT NULL,
  CONSTRAINT "pk_study_plan_course_map" PRIMARY KEY ("map_id")
);
INSERT INTO "study_plan_course_map" VALUES('M-01','BSCS-2022','BSCS-2025','renumbering','CSC101','COMP101','R-01',0);
INSERT INTO "study_plan_course_map" VALUES('M-02','BSCS-2022','BSCS-2025','renumbering','CSC201','COMP201','R-02',0);
INSERT INTO "study_plan_course_map" VALUES('M-03','BSCS-2022','BSCS-2025','split','COMP250','COMP251, COMP252','R-05',0);
INSERT INTO "study_plan_course_map" VALUES('M-04','BSCS-2022','BSCS-2025','credit_change','COMP400','COMP400','R-07',0);
INSERT INTO "study_plan_course_map" VALUES('M-05','BSDS-2022','BSDS-2025','renumbering','CSC101','COMP101','R-01',0);
INSERT INTO "study_plan_course_map" VALUES('M-06','BSDS-2022','BSDS-2025','renumbering','CSC201','COMP201','R-02',0);
INSERT INTO "study_plan_course_map" VALUES('M-07','BSDS-2022','BSDS-2025','merge','DSCI200, DSCI205','DSCI210','R-06',0);
INSERT INTO "study_plan_course_map" VALUES('M-08','BBA-2022','BBA-2025','retired','CIS220','IT220','R-10',1);
INSERT INTO "study_plan_course_map" VALUES('M-09','BSCS-2022','BSDS-2025','program_change','CSC101','COMP101','R-01',0);
INSERT INTO "study_plan_course_map" VALUES('M-10','BSCS-2022','BSDS-2025','program_change','CSC201','COMP201','R-02',0);
INSERT INTO "study_plan_course_map" VALUES('M-11','BSCS-2022','BSDS-2025','program_change','COMP430','DSCI410','R-03',0);
INSERT INTO "study_plan_course_map" VALUES('M-12','BBA-2022','BSBAN-2025','program_change','COMP340','MIS340','R-04',0);
CREATE TABLE "study_plan_detail" (
  "line_id" TEXT NOT NULL,
  "version_id" TEXT NOT NULL,
  "requirement_id" TEXT NOT NULL,
  "course_code" TEXT NOT NULL,
  "credits" INTEGER NOT NULL,
  "min_grade" TEXT NOT NULL,
  "year_level" INTEGER NOT NULL,
  "semester" TEXT NOT NULL,
  "gened_category" TEXT,
  "mandate" TEXT,
  "min_credits_completed" INTEGER,
  CONSTRAINT "pk_study_plan_detail" PRIMARY KEY ("line_id")
);
INSERT INTO "study_plan_detail" VALUES('BBA-2022:G01','BBA-2022','GENED','ENGL101',3,'D',1,'Fall','Communication',NULL,NULL);
INSERT INTO "study_plan_detail" VALUES('BBA-2022:G02','BBA-2022','GENED','ENGL102',3,'D',1,'Spring','Communication','advanced_english',NULL);
INSERT INTO "study_plan_detail" VALUES('BBA-2022:G03','BBA-2022','GENED','UAES101',3,'D',1,'Fall','UAE Studies','emirati_studies',NULL);
INSERT INTO "study_plan_detail" VALUES('BBA-2022:C01','BBA-2022','MAJOR-CORE','ECON101',3,'D',1,'Fall',NULL,NULL,NULL);
INSERT INTO "study_plan_detail" VALUES('BBA-2022:C02','BBA-2022','MAJOR-CORE','ECON102',3,'D',1,'Spring',NULL,NULL,NULL);
INSERT INTO "study_plan_detail" VALUES('BBA-2022:C03','BBA-2022','MAJOR-CORE','MGMT201',3,'D',2,'Fall',NULL,NULL,NULL);
INSERT INTO "study_plan_detail" VALUES('BBA-2022:C04','BBA-2022','MAJOR-CORE','ACCT201',3,'D',2,'Fall',NULL,NULL,NULL);
INSERT INTO "study_plan_detail" VALUES('BBA-2022:C05','BBA-2022','MAJOR-CORE','MKTG201',3,'D',2,'Fall',NULL,NULL,NULL);
INSERT INTO "study_plan_detail" VALUES('BBA-2022:C06','BBA-2022','MAJOR-CORE','STAT201',3,'D',2,'Fall',NULL,NULL,NULL);
INSERT INTO "study_plan_detail" VALUES('BBA-2022:C07','BBA-2022','MAJOR-CORE','CIS220',3,'D',2,'Fall',NULL,NULL,NULL);
INSERT INTO "study_plan_detail" VALUES('BBA-2022:C08','BBA-2022','MAJOR-CORE','ACCT202',3,'D',2,'Spring',NULL,NULL,NULL);
INSERT INTO "study_plan_detail" VALUES('BBA-2022:C09','BBA-2022','MAJOR-CORE','QM210',3,'D',2,'Spring',NULL,NULL,NULL);
INSERT INTO "study_plan_detail" VALUES('BBA-2022:C10','BBA-2022','MAJOR-CORE','BUSL210',3,'D',2,'Spring',NULL,NULL,NULL);
INSERT INTO "study_plan_detail" VALUES('BBA-2022:C11','BBA-2022','MAJOR-CORE','FIN301',3,'D',3,'Fall',NULL,NULL,NULL);
INSERT INTO "study_plan_detail" VALUES('BBA-2022:C12','BBA-2022','MAJOR-CORE','MGMT320',3,'D',3,'Fall',NULL,NULL,NULL);
INSERT INTO "study_plan_detail" VALUES('BBA-2022:C13','BBA-2022','MAJOR-CORE','MGMT310',3,'D',3,'Spring',NULL,NULL,NULL);
INSERT INTO "study_plan_detail" VALUES('BBA-2022:C14','BBA-2022','MAJOR-CORE','MKTG310',3,'D',3,'Spring',NULL,NULL,NULL);
INSERT INTO "study_plan_detail" VALUES('BBA-2022:C15','BBA-2022','MAJOR-CORE','BUSA450',3,'D',4,'Spring',NULL,NULL,NULL);
INSERT INTO "study_plan_detail" VALUES('BBA-2022:I01','BBA-2022','INTERN','INTR390',6,'D',4,'Spring',NULL,NULL,90);
INSERT INTO "study_plan_detail" VALUES('BBA-2025:G01','BBA-2025','GENED','ENGL101',3,'D',1,'Fall','Communication',NULL,NULL);
INSERT INTO "study_plan_detail" VALUES('BBA-2025:G02','BBA-2025','GENED','ENGL102',3,'D',1,'Spring','Communication','advanced_english',NULL);
INSERT INTO "study_plan_detail" VALUES('BBA-2025:G03','BBA-2025','GENED','UAES101',3,'D',1,'Fall','UAE Studies','emirati_studies',NULL);
INSERT INTO "study_plan_detail" VALUES('BBA-2025:G04','BBA-2025','GENED','INNO201',3,'D',2,'Fall','Innovation','innovation',NULL);
INSERT INTO "study_plan_detail" VALUES('BBA-2025:C01','BBA-2025','MAJOR-CORE','ECON101',3,'D',1,'Fall',NULL,NULL,NULL);
INSERT INTO "study_plan_detail" VALUES('BBA-2025:C02','BBA-2025','MAJOR-CORE','ECON102',3,'D',1,'Spring',NULL,NULL,NULL);
INSERT INTO "study_plan_detail" VALUES('BBA-2025:C03','BBA-2025','MAJOR-CORE','MGMT201',3,'D',2,'Fall',NULL,NULL,NULL);
INSERT INTO "study_plan_detail" VALUES('BBA-2025:C04','BBA-2025','MAJOR-CORE','ACCT201',3,'D',2,'Fall',NULL,NULL,NULL);
INSERT INTO "study_plan_detail" VALUES('BBA-2025:C05','BBA-2025','MAJOR-CORE','MKTG201',3,'D',2,'Fall',NULL,NULL,NULL);
INSERT INTO "study_plan_detail" VALUES('BBA-2025:C06','BBA-2025','MAJOR-CORE','STAT201',3,'D',2,'Fall',NULL,NULL,NULL);
INSERT INTO "study_plan_detail" VALUES('BBA-2025:C07','BBA-2025','MAJOR-CORE','IT220',3,'D',2,'Fall',NULL,NULL,NULL);
INSERT INTO "study_plan_detail" VALUES('BBA-2025:C08','BBA-2025','MAJOR-CORE','ACCT202',3,'D',2,'Spring',NULL,NULL,NULL);
INSERT INTO "study_plan_detail" VALUES('BBA-2025:C09','BBA-2025','MAJOR-CORE','BUSL210',3,'D',2,'Spring',NULL,NULL,NULL);
INSERT INTO "study_plan_detail" VALUES('BBA-2025:C10','BBA-2025','MAJOR-CORE','FIN301',3,'D',3,'Fall',NULL,NULL,NULL);
INSERT INTO "study_plan_detail" VALUES('BBA-2025:C11','BBA-2025','MAJOR-CORE','MGMT320',3,'D',3,'Fall',NULL,NULL,NULL);
INSERT INTO "study_plan_detail" VALUES('BBA-2025:C12','BBA-2025','MAJOR-CORE','MGMT310',3,'D',3,'Spring',NULL,NULL,NULL);
INSERT INTO "study_plan_detail" VALUES('BBA-2025:C13','BBA-2025','MAJOR-CORE','MKTG310',3,'D',3,'Spring',NULL,NULL,NULL);
INSERT INTO "study_plan_detail" VALUES('BBA-2025:C14','BBA-2025','MAJOR-CORE','FIN310',3,'D',3,'Spring',NULL,NULL,NULL);
INSERT INTO "study_plan_detail" VALUES('BBA-2025:C15','BBA-2025','MAJOR-CORE','MGMT410',3,'D',4,'Fall',NULL,NULL,NULL);
INSERT INTO "study_plan_detail" VALUES('BBA-2025:C16','BBA-2025','MAJOR-CORE','BUSA450',3,'D',4,'Spring',NULL,NULL,NULL);
INSERT INTO "study_plan_detail" VALUES('BBA-2025:I01','BBA-2025','INTERN','INTR390',6,'D',4,'Spring',NULL,NULL,90);
INSERT INTO "study_plan_detail" VALUES('BSBAN-2025:G01','BSBAN-2025','GENED','ENGL101',3,'D',1,'Fall','Communication',NULL,NULL);
INSERT INTO "study_plan_detail" VALUES('BSBAN-2025:G02','BSBAN-2025','GENED','ENGL102',3,'D',1,'Spring','Communication','advanced_english',NULL);
INSERT INTO "study_plan_detail" VALUES('BSBAN-2025:G03','BSBAN-2025','GENED','UAES101',3,'D',1,'Fall','UAE Studies','emirati_studies',NULL);
INSERT INTO "study_plan_detail" VALUES('BSBAN-2025:G04','BSBAN-2025','GENED','INNO201',3,'D',2,'Fall','Innovation','innovation',NULL);
INSERT INTO "study_plan_detail" VALUES('BSBAN-2025:C01','BSBAN-2025','MAJOR-CORE','ECON101',3,'C',1,'Fall',NULL,NULL,NULL);
INSERT INTO "study_plan_detail" VALUES('BSBAN-2025:C02','BSBAN-2025','MAJOR-CORE','MATH101',3,'C',1,'Fall',NULL,NULL,NULL);
INSERT INTO "study_plan_detail" VALUES('BSBAN-2025:C03','BSBAN-2025','MAJOR-CORE','COMP101',3,'C',1,'Spring',NULL,NULL,NULL);
INSERT INTO "study_plan_detail" VALUES('BSBAN-2025:C04','BSBAN-2025','MAJOR-CORE','MGMT201',3,'C',2,'Fall',NULL,NULL,NULL);
INSERT INTO "study_plan_detail" VALUES('BSBAN-2025:C05','BSBAN-2025','MAJOR-CORE','ACCT201',3,'C',2,'Fall',NULL,NULL,NULL);
INSERT INTO "study_plan_detail" VALUES('BSBAN-2025:C06','BSBAN-2025','MAJOR-CORE','STAT201',3,'C',2,'Fall',NULL,NULL,NULL);
INSERT INTO "study_plan_detail" VALUES('BSBAN-2025:C07','BSBAN-2025','MAJOR-CORE','IT220',3,'C',2,'Fall',NULL,NULL,NULL);
INSERT INTO "study_plan_detail" VALUES('BSBAN-2025:C08','BSBAN-2025','MAJOR-CORE','DSCI210',3,'C',2,'Spring',NULL,NULL,NULL);
INSERT INTO "study_plan_detail" VALUES('BSBAN-2025:C09','BSBAN-2025','MAJOR-CORE','BUSA210',3,'C',2,'Spring',NULL,NULL,NULL);
INSERT INTO "study_plan_detail" VALUES('BSBAN-2025:C10','BSBAN-2025','MAJOR-CORE','MIS340',3,'C',3,'Fall',NULL,NULL,NULL);
INSERT INTO "study_plan_detail" VALUES('BSBAN-2025:C11','BSBAN-2025','MAJOR-CORE','DSCI320',3,'C',3,'Fall',NULL,NULL,NULL);
INSERT INTO "study_plan_detail" VALUES('BSBAN-2025:C12','BSBAN-2025','MAJOR-CORE','ECON301',3,'C',3,'Fall',NULL,NULL,NULL);
INSERT INTO "study_plan_detail" VALUES('BSBAN-2025:C13','BSBAN-2025','MAJOR-CORE','BUSA320',3,'C',3,'Fall',NULL,NULL,NULL);
INSERT INTO "study_plan_detail" VALUES('BSBAN-2025:C14','BSBAN-2025','MAJOR-CORE','BUSA330',3,'C',3,'Spring',NULL,NULL,NULL);
INSERT INTO "study_plan_detail" VALUES('BSBAN-2025:C15','BSBAN-2025','MAJOR-CORE','BUSA310',3,'C',3,'Spring',NULL,NULL,NULL);
INSERT INTO "study_plan_detail" VALUES('BSBAN-2025:C16','BSBAN-2025','MAJOR-CORE','DSCI450',3,'C',4,'Fall',NULL,NULL,NULL);
INSERT INTO "study_plan_detail" VALUES('BSBAN-2025:C17','BSBAN-2025','MAJOR-CORE','BUSA450',3,'C',4,'Spring',NULL,NULL,NULL);
INSERT INTO "study_plan_detail" VALUES('BSBAN-2025:I01','BSBAN-2025','INTERN','INTR390',6,'D',4,'Spring',NULL,NULL,90);
INSERT INTO "study_plan_detail" VALUES('BSCS-2022:G01','BSCS-2022','GENED','ENGL101',3,'D',1,'Fall','Communication',NULL,NULL);
INSERT INTO "study_plan_detail" VALUES('BSCS-2022:G02','BSCS-2022','GENED','ENGL102',3,'D',1,'Spring','Communication','advanced_english',NULL);
INSERT INTO "study_plan_detail" VALUES('BSCS-2022:G03','BSCS-2022','GENED','UAES101',3,'D',1,'Fall','UAE Studies','emirati_studies',NULL);
INSERT INTO "study_plan_detail" VALUES('BSCS-2022:C01','BSCS-2022','MAJOR-CORE','MATH101',3,'C',1,'Fall',NULL,NULL,NULL);
INSERT INTO "study_plan_detail" VALUES('BSCS-2022:C02','BSCS-2022','MAJOR-CORE','MATH102',3,'C',1,'Spring',NULL,NULL,NULL);
INSERT INTO "study_plan_detail" VALUES('BSCS-2022:C03','BSCS-2022','MAJOR-CORE','CSC101',3,'C',1,'Fall',NULL,NULL,NULL);
INSERT INTO "study_plan_detail" VALUES('BSCS-2022:C04','BSCS-2022','MAJOR-CORE','CSC201',3,'C',1,'Spring',NULL,NULL,NULL);
INSERT INTO "study_plan_detail" VALUES('BSCS-2022:C05','BSCS-2022','MAJOR-CORE','MATH210',3,'C',2,'Spring',NULL,NULL,NULL);
INSERT INTO "study_plan_detail" VALUES('BSCS-2022:C06','BSCS-2022','MAJOR-CORE','STAT201',3,'C',2,'Fall',NULL,NULL,NULL);
INSERT INTO "study_plan_detail" VALUES('BSCS-2022:C07','BSCS-2022','MAJOR-CORE','COMP210',3,'C',2,'Fall',NULL,NULL,NULL);
INSERT INTO "study_plan_detail" VALUES('BSCS-2022:C08','BSCS-2022','MAJOR-CORE','COMP220',3,'C',2,'Fall',NULL,NULL,NULL);
INSERT INTO "study_plan_detail" VALUES('BSCS-2022:C09','BSCS-2022','MAJOR-CORE','COMP230',3,'C',2,'Spring',NULL,NULL,NULL);
INSERT INTO "study_plan_detail" VALUES('BSCS-2022:C10','BSCS-2022','MAJOR-CORE','COMP250',4,'C',2,'Spring',NULL,NULL,NULL);
INSERT INTO "study_plan_detail" VALUES('BSCS-2022:C11','BSCS-2022','MAJOR-CORE','COMP301',3,'C',3,'Fall',NULL,NULL,NULL);
INSERT INTO "study_plan_detail" VALUES('BSCS-2022:C12','BSCS-2022','MAJOR-CORE','COMP320',3,'C',3,'Fall',NULL,NULL,NULL);
INSERT INTO "study_plan_detail" VALUES('BSCS-2022:C13','BSCS-2022','MAJOR-CORE','COMP340',3,'C',3,'Fall',NULL,NULL,NULL);
INSERT INTO "study_plan_detail" VALUES('BSCS-2022:C14','BSCS-2022','MAJOR-CORE','COMP310',3,'C',3,'Spring',NULL,NULL,NULL);
INSERT INTO "study_plan_detail" VALUES('BSCS-2022:C15','BSCS-2022','MAJOR-CORE','COMP315',3,'C',3,'Spring',NULL,NULL,NULL);
INSERT INTO "study_plan_detail" VALUES('BSCS-2022:C16','BSCS-2022','MAJOR-CORE','COMP395',2,'C',3,'Spring',NULL,NULL,NULL);
INSERT INTO "study_plan_detail" VALUES('BSCS-2022:C17','BSCS-2022','MAJOR-CORE','COMP400',3,'C',4,'Fall',NULL,NULL,NULL);
INSERT INTO "study_plan_detail" VALUES('BSCS-2022:I01','BSCS-2022','INTERN','INTR390',6,'D',4,'Spring',NULL,NULL,90);
INSERT INTO "study_plan_detail" VALUES('BSCS-2025:G01','BSCS-2025','GENED','ENGL101',3,'D',1,'Fall','Communication',NULL,NULL);
INSERT INTO "study_plan_detail" VALUES('BSCS-2025:G02','BSCS-2025','GENED','ENGL102',3,'D',1,'Spring','Communication','advanced_english',NULL);
INSERT INTO "study_plan_detail" VALUES('BSCS-2025:G03','BSCS-2025','GENED','UAES101',3,'D',1,'Fall','UAE Studies','emirati_studies',NULL);
INSERT INTO "study_plan_detail" VALUES('BSCS-2025:G04','BSCS-2025','GENED','INNO201',3,'D',2,'Fall','Innovation','innovation',NULL);
INSERT INTO "study_plan_detail" VALUES('BSCS-2025:C01','BSCS-2025','MAJOR-CORE','MATH101',3,'C',1,'Fall',NULL,NULL,NULL);
INSERT INTO "study_plan_detail" VALUES('BSCS-2025:C02','BSCS-2025','MAJOR-CORE','MATH102',3,'C',1,'Spring',NULL,NULL,NULL);
INSERT INTO "study_plan_detail" VALUES('BSCS-2025:C03','BSCS-2025','MAJOR-CORE','COMP101',3,'C',1,'Fall',NULL,NULL,NULL);
INSERT INTO "study_plan_detail" VALUES('BSCS-2025:C04','BSCS-2025','MAJOR-CORE','COMP201',3,'C',1,'Spring',NULL,NULL,NULL);
INSERT INTO "study_plan_detail" VALUES('BSCS-2025:C05','BSCS-2025','MAJOR-CORE','MATH210',3,'C',2,'Spring',NULL,NULL,NULL);
INSERT INTO "study_plan_detail" VALUES('BSCS-2025:C06','BSCS-2025','MAJOR-CORE','STAT201',3,'C',2,'Fall',NULL,NULL,NULL);
INSERT INTO "study_plan_detail" VALUES('BSCS-2025:C07','BSCS-2025','MAJOR-CORE','COMP210',3,'C',2,'Fall',NULL,NULL,NULL);
INSERT INTO "study_plan_detail" VALUES('BSCS-2025:C08','BSCS-2025','MAJOR-CORE','COMP220',3,'C',2,'Fall',NULL,NULL,NULL);
INSERT INTO "study_plan_detail" VALUES('BSCS-2025:C09','BSCS-2025','MAJOR-CORE','COMP230',3,'C',2,'Spring',NULL,NULL,NULL);
INSERT INTO "study_plan_detail" VALUES('BSCS-2025:C10','BSCS-2025','MAJOR-CORE','COMP251',3,'C',2,'Spring',NULL,NULL,NULL);
INSERT INTO "study_plan_detail" VALUES('BSCS-2025:C11','BSCS-2025','MAJOR-CORE','COMP252',3,'C',2,'Spring',NULL,NULL,NULL);
INSERT INTO "study_plan_detail" VALUES('BSCS-2025:C12','BSCS-2025','MAJOR-CORE','COMP301',3,'C',3,'Fall',NULL,NULL,NULL);
INSERT INTO "study_plan_detail" VALUES('BSCS-2025:C13','BSCS-2025','MAJOR-CORE','COMP320',3,'C',3,'Fall',NULL,NULL,NULL);
INSERT INTO "study_plan_detail" VALUES('BSCS-2025:C14','BSCS-2025','MAJOR-CORE','COMP340',3,'C',3,'Fall',NULL,NULL,NULL);
INSERT INTO "study_plan_detail" VALUES('BSCS-2025:C15','BSCS-2025','MAJOR-CORE','COMP310',3,'C',3,'Spring',NULL,NULL,NULL);
INSERT INTO "study_plan_detail" VALUES('BSCS-2025:C16','BSCS-2025','MAJOR-CORE','COMP315',3,'C',3,'Spring',NULL,NULL,NULL);
INSERT INTO "study_plan_detail" VALUES('BSCS-2025:C17','BSCS-2025','MAJOR-CORE','COMP395',2,'C',3,'Spring',NULL,NULL,NULL);
INSERT INTO "study_plan_detail" VALUES('BSCS-2025:C18','BSCS-2025','MAJOR-CORE','COMP400',4,'C',4,'Fall',NULL,NULL,NULL);
INSERT INTO "study_plan_detail" VALUES('BSCS-2025:I01','BSCS-2025','INTERN','INTR390',6,'D',4,'Spring',NULL,NULL,90);
INSERT INTO "study_plan_detail" VALUES('BSCS-2025-AI:G01','BSCS-2025-AI','GENED','ENGL101',3,'D',1,'Fall','Communication',NULL,NULL);
INSERT INTO "study_plan_detail" VALUES('BSCS-2025-AI:G02','BSCS-2025-AI','GENED','ENGL102',3,'D',1,'Spring','Communication','advanced_english',NULL);
INSERT INTO "study_plan_detail" VALUES('BSCS-2025-AI:G03','BSCS-2025-AI','GENED','UAES101',3,'D',1,'Fall','UAE Studies','emirati_studies',NULL);
INSERT INTO "study_plan_detail" VALUES('BSCS-2025-AI:G04','BSCS-2025-AI','GENED','INNO201',3,'D',2,'Fall','Innovation','innovation',NULL);
INSERT INTO "study_plan_detail" VALUES('BSCS-2025-AI:C01','BSCS-2025-AI','MAJOR-CORE','MATH101',3,'C',1,'Fall',NULL,NULL,NULL);
INSERT INTO "study_plan_detail" VALUES('BSCS-2025-AI:C02','BSCS-2025-AI','MAJOR-CORE','MATH102',3,'C',1,'Spring',NULL,NULL,NULL);
INSERT INTO "study_plan_detail" VALUES('BSCS-2025-AI:C03','BSCS-2025-AI','MAJOR-CORE','COMP101',3,'C',1,'Fall',NULL,NULL,NULL);
INSERT INTO "study_plan_detail" VALUES('BSCS-2025-AI:C04','BSCS-2025-AI','MAJOR-CORE','COMP201',3,'C',1,'Spring',NULL,NULL,NULL);
INSERT INTO "study_plan_detail" VALUES('BSCS-2025-AI:C05','BSCS-2025-AI','MAJOR-CORE','MATH210',3,'C',2,'Spring',NULL,NULL,NULL);
INSERT INTO "study_plan_detail" VALUES('BSCS-2025-AI:C06','BSCS-2025-AI','MAJOR-CORE','STAT201',3,'C',2,'Fall',NULL,NULL,NULL);
INSERT INTO "study_plan_detail" VALUES('BSCS-2025-AI:C07','BSCS-2025-AI','MAJOR-CORE','COMP210',3,'C',2,'Fall',NULL,NULL,NULL);
INSERT INTO "study_plan_detail" VALUES('BSCS-2025-AI:C08','BSCS-2025-AI','MAJOR-CORE','COMP220',3,'C',2,'Fall',NULL,NULL,NULL);
INSERT INTO "study_plan_detail" VALUES('BSCS-2025-AI:C09','BSCS-2025-AI','MAJOR-CORE','COMP230',3,'C',2,'Spring',NULL,NULL,NULL);
INSERT INTO "study_plan_detail" VALUES('BSCS-2025-AI:C10','BSCS-2025-AI','MAJOR-CORE','COMP251',3,'C',2,'Spring',NULL,NULL,NULL);
INSERT INTO "study_plan_detail" VALUES('BSCS-2025-AI:C11','BSCS-2025-AI','MAJOR-CORE','COMP252',3,'C',2,'Spring',NULL,NULL,NULL);
INSERT INTO "study_plan_detail" VALUES('BSCS-2025-AI:C12','BSCS-2025-AI','MAJOR-CORE','COMP301',3,'C',3,'Fall',NULL,NULL,NULL);
INSERT INTO "study_plan_detail" VALUES('BSCS-2025-AI:C13','BSCS-2025-AI','MAJOR-CORE','COMP320',3,'C',3,'Fall',NULL,NULL,NULL);
INSERT INTO "study_plan_detail" VALUES('BSCS-2025-AI:C14','BSCS-2025-AI','MAJOR-CORE','COMP340',3,'C',3,'Fall',NULL,NULL,NULL);
INSERT INTO "study_plan_detail" VALUES('BSCS-2025-AI:C15','BSCS-2025-AI','MAJOR-CORE','COMP310',3,'C',3,'Spring',NULL,NULL,NULL);
INSERT INTO "study_plan_detail" VALUES('BSCS-2025-AI:C16','BSCS-2025-AI','MAJOR-CORE','COMP315',3,'C',3,'Spring',NULL,NULL,NULL);
INSERT INTO "study_plan_detail" VALUES('BSCS-2025-AI:C17','BSCS-2025-AI','MAJOR-CORE','COMP395',2,'C',3,'Spring',NULL,NULL,NULL);
INSERT INTO "study_plan_detail" VALUES('BSCS-2025-AI:C18','BSCS-2025-AI','MAJOR-CORE','COMP400',4,'C',4,'Fall',NULL,NULL,NULL);
INSERT INTO "study_plan_detail" VALUES('BSCS-2025-AI:T01','BSCS-2025-AI','MAJOR-CORE','COMP430',3,'C',3,'Spring',NULL,NULL,NULL);
INSERT INTO "study_plan_detail" VALUES('BSCS-2025-AI:T02','BSCS-2025-AI','MAJOR-CORE','DSCI410',3,'C',4,'Fall',NULL,NULL,NULL);
INSERT INTO "study_plan_detail" VALUES('BSCS-2025-AI:I01','BSCS-2025-AI','INTERN','INTR390',6,'D',4,'Spring',NULL,NULL,90);
INSERT INTO "study_plan_detail" VALUES('BSDS-2022:G01','BSDS-2022','GENED','ENGL101',3,'D',1,'Fall','Communication',NULL,NULL);
INSERT INTO "study_plan_detail" VALUES('BSDS-2022:G02','BSDS-2022','GENED','ENGL102',3,'D',1,'Spring','Communication','advanced_english',NULL);
INSERT INTO "study_plan_detail" VALUES('BSDS-2022:G03','BSDS-2022','GENED','UAES101',3,'D',1,'Fall','UAE Studies','emirati_studies',NULL);
INSERT INTO "study_plan_detail" VALUES('BSDS-2022:C01','BSDS-2022','MAJOR-CORE','MATH101',3,'C',1,'Fall',NULL,NULL,NULL);
INSERT INTO "study_plan_detail" VALUES('BSDS-2022:C02','BSDS-2022','MAJOR-CORE','MATH102',3,'C',1,'Spring',NULL,NULL,NULL);
INSERT INTO "study_plan_detail" VALUES('BSDS-2022:C03','BSDS-2022','MAJOR-CORE','CSC101',3,'C',1,'Fall',NULL,NULL,NULL);
INSERT INTO "study_plan_detail" VALUES('BSDS-2022:C04','BSDS-2022','MAJOR-CORE','CSC201',3,'C',1,'Spring',NULL,NULL,NULL);
INSERT INTO "study_plan_detail" VALUES('BSDS-2022:C05','BSDS-2022','MAJOR-CORE','MATH210',3,'C',2,'Spring',NULL,NULL,NULL);
INSERT INTO "study_plan_detail" VALUES('BSDS-2022:C06','BSDS-2022','MAJOR-CORE','STAT201',3,'C',2,'Fall',NULL,NULL,NULL);
INSERT INTO "study_plan_detail" VALUES('BSDS-2022:C07','BSDS-2022','MAJOR-CORE','DSCI200',4,'C',2,'Fall',NULL,NULL,NULL);
INSERT INTO "study_plan_detail" VALUES('BSDS-2022:C08','BSDS-2022','MAJOR-CORE','DSCI205',2,'C',2,'Spring',NULL,NULL,NULL);
INSERT INTO "study_plan_detail" VALUES('BSDS-2022:C09','BSDS-2022','MAJOR-CORE','DSCI220',3,'C',2,'Fall',NULL,NULL,NULL);
INSERT INTO "study_plan_detail" VALUES('BSDS-2022:C10','BSDS-2022','MAJOR-CORE','DSCI230',3,'C',2,'Spring',NULL,NULL,NULL);
INSERT INTO "study_plan_detail" VALUES('BSDS-2022:C11','BSDS-2022','MAJOR-CORE','COMP340',3,'C',3,'Fall',NULL,NULL,NULL);
INSERT INTO "study_plan_detail" VALUES('BSDS-2022:C12','BSDS-2022','MAJOR-CORE','DSCI320',3,'C',3,'Fall',NULL,NULL,NULL);
INSERT INTO "study_plan_detail" VALUES('BSDS-2022:C13','BSDS-2022','MAJOR-CORE','DSCI310',3,'C',3,'Spring',NULL,NULL,NULL);
INSERT INTO "study_plan_detail" VALUES('BSDS-2022:C14','BSDS-2022','MAJOR-CORE','DSCI330',3,'C',3,'Spring',NULL,NULL,NULL);
INSERT INTO "study_plan_detail" VALUES('BSDS-2022:C15','BSDS-2022','MAJOR-CORE','DSCI410',3,'C',4,'Spring',NULL,NULL,NULL);
INSERT INTO "study_plan_detail" VALUES('BSDS-2022:C16','BSDS-2022','MAJOR-CORE','COMP400',3,'C',4,'Fall',NULL,NULL,NULL);
INSERT INTO "study_plan_detail" VALUES('BSDS-2022:I01','BSDS-2022','INTERN','INTR390',6,'D',4,'Spring',NULL,NULL,90);
INSERT INTO "study_plan_detail" VALUES('BSDS-2025:G01','BSDS-2025','GENED','ENGL101',3,'D',1,'Fall','Communication',NULL,NULL);
INSERT INTO "study_plan_detail" VALUES('BSDS-2025:G02','BSDS-2025','GENED','ENGL102',3,'D',1,'Spring','Communication','advanced_english',NULL);
INSERT INTO "study_plan_detail" VALUES('BSDS-2025:G03','BSDS-2025','GENED','UAES101',3,'D',1,'Fall','UAE Studies','emirati_studies',NULL);
INSERT INTO "study_plan_detail" VALUES('BSDS-2025:G04','BSDS-2025','GENED','INNO201',3,'D',2,'Fall','Innovation','innovation',NULL);
INSERT INTO "study_plan_detail" VALUES('BSDS-2025:C01','BSDS-2025','MAJOR-CORE','MATH101',3,'C',1,'Fall',NULL,NULL,NULL);
INSERT INTO "study_plan_detail" VALUES('BSDS-2025:C02','BSDS-2025','MAJOR-CORE','MATH102',3,'C',1,'Spring',NULL,NULL,NULL);
INSERT INTO "study_plan_detail" VALUES('BSDS-2025:C03','BSDS-2025','MAJOR-CORE','COMP101',3,'C',1,'Fall',NULL,NULL,NULL);
INSERT INTO "study_plan_detail" VALUES('BSDS-2025:C04','BSDS-2025','MAJOR-CORE','COMP201',3,'C',1,'Spring',NULL,NULL,NULL);
INSERT INTO "study_plan_detail" VALUES('BSDS-2025:C05','BSDS-2025','MAJOR-CORE','MATH210',3,'C',2,'Spring',NULL,NULL,NULL);
INSERT INTO "study_plan_detail" VALUES('BSDS-2025:C06','BSDS-2025','MAJOR-CORE','STAT201',3,'C',2,'Fall',NULL,NULL,NULL);
INSERT INTO "study_plan_detail" VALUES('BSDS-2025:C07','BSDS-2025','MAJOR-CORE','DSCI210',3,'C',2,'Fall',NULL,NULL,NULL);
INSERT INTO "study_plan_detail" VALUES('BSDS-2025:C08','BSDS-2025','MAJOR-CORE','DSCI220',3,'C',2,'Fall',NULL,NULL,NULL);
INSERT INTO "study_plan_detail" VALUES('BSDS-2025:C09','BSDS-2025','MAJOR-CORE','DSCI230',3,'C',2,'Spring',NULL,NULL,NULL);
INSERT INTO "study_plan_detail" VALUES('BSDS-2025:C10','BSDS-2025','MAJOR-CORE','COMP340',3,'C',3,'Fall',NULL,NULL,NULL);
INSERT INTO "study_plan_detail" VALUES('BSDS-2025:C11','BSDS-2025','MAJOR-CORE','DSCI320',3,'C',3,'Fall',NULL,NULL,NULL);
INSERT INTO "study_plan_detail" VALUES('BSDS-2025:C12','BSDS-2025','MAJOR-CORE','DSCI310',3,'C',3,'Spring',NULL,NULL,NULL);
INSERT INTO "study_plan_detail" VALUES('BSDS-2025:C13','BSDS-2025','MAJOR-CORE','DSCI330',3,'C',3,'Spring',NULL,NULL,NULL);
INSERT INTO "study_plan_detail" VALUES('BSDS-2025:C14','BSDS-2025','MAJOR-CORE','DSCI395',2,'C',3,'Spring',NULL,NULL,NULL);
INSERT INTO "study_plan_detail" VALUES('BSDS-2025:C15','BSDS-2025','MAJOR-CORE','DSCI410',3,'C',4,'Spring',NULL,NULL,NULL);
INSERT INTO "study_plan_detail" VALUES('BSDS-2025:C16','BSDS-2025','MAJOR-CORE','COMP400',4,'C',4,'Fall',NULL,NULL,NULL);
INSERT INTO "study_plan_detail" VALUES('BSDS-2025:I01','BSDS-2025','INTERN','INTR390',6,'D',4,'Spring',NULL,NULL,90);
INSERT INTO "study_plan_detail" VALUES('BSDS-2025-BA:G01','BSDS-2025-BA','GENED','ENGL101',3,'D',1,'Fall','Communication',NULL,NULL);
INSERT INTO "study_plan_detail" VALUES('BSDS-2025-BA:G02','BSDS-2025-BA','GENED','ENGL102',3,'D',1,'Spring','Communication','advanced_english',NULL);
INSERT INTO "study_plan_detail" VALUES('BSDS-2025-BA:G03','BSDS-2025-BA','GENED','UAES101',3,'D',1,'Fall','UAE Studies','emirati_studies',NULL);
INSERT INTO "study_plan_detail" VALUES('BSDS-2025-BA:G04','BSDS-2025-BA','GENED','INNO201',3,'D',2,'Fall','Innovation','innovation',NULL);
INSERT INTO "study_plan_detail" VALUES('BSDS-2025-BA:C01','BSDS-2025-BA','MAJOR-CORE','MATH101',3,'C',1,'Fall',NULL,NULL,NULL);
INSERT INTO "study_plan_detail" VALUES('BSDS-2025-BA:C02','BSDS-2025-BA','MAJOR-CORE','MATH102',3,'C',1,'Spring',NULL,NULL,NULL);
INSERT INTO "study_plan_detail" VALUES('BSDS-2025-BA:C03','BSDS-2025-BA','MAJOR-CORE','COMP101',3,'C',1,'Fall',NULL,NULL,NULL);
INSERT INTO "study_plan_detail" VALUES('BSDS-2025-BA:C04','BSDS-2025-BA','MAJOR-CORE','COMP201',3,'C',1,'Spring',NULL,NULL,NULL);
INSERT INTO "study_plan_detail" VALUES('BSDS-2025-BA:C05','BSDS-2025-BA','MAJOR-CORE','MATH210',3,'C',2,'Spring',NULL,NULL,NULL);
INSERT INTO "study_plan_detail" VALUES('BSDS-2025-BA:C06','BSDS-2025-BA','MAJOR-CORE','STAT201',3,'C',2,'Fall',NULL,NULL,NULL);
INSERT INTO "study_plan_detail" VALUES('BSDS-2025-BA:C07','BSDS-2025-BA','MAJOR-CORE','DSCI210',3,'C',2,'Fall',NULL,NULL,NULL);
INSERT INTO "study_plan_detail" VALUES('BSDS-2025-BA:C08','BSDS-2025-BA','MAJOR-CORE','DSCI220',3,'C',2,'Fall',NULL,NULL,NULL);
INSERT INTO "study_plan_detail" VALUES('BSDS-2025-BA:C09','BSDS-2025-BA','MAJOR-CORE','DSCI230',3,'C',2,'Spring',NULL,NULL,NULL);
INSERT INTO "study_plan_detail" VALUES('BSDS-2025-BA:C10','BSDS-2025-BA','MAJOR-CORE','COMP340',3,'C',3,'Fall',NULL,NULL,NULL);
INSERT INTO "study_plan_detail" VALUES('BSDS-2025-BA:C11','BSDS-2025-BA','MAJOR-CORE','DSCI320',3,'C',3,'Fall',NULL,NULL,NULL);
INSERT INTO "study_plan_detail" VALUES('BSDS-2025-BA:C12','BSDS-2025-BA','MAJOR-CORE','DSCI310',3,'C',3,'Spring',NULL,NULL,NULL);
INSERT INTO "study_plan_detail" VALUES('BSDS-2025-BA:C13','BSDS-2025-BA','MAJOR-CORE','DSCI330',3,'C',3,'Spring',NULL,NULL,NULL);
INSERT INTO "study_plan_detail" VALUES('BSDS-2025-BA:C14','BSDS-2025-BA','MAJOR-CORE','DSCI395',2,'C',3,'Spring',NULL,NULL,NULL);
INSERT INTO "study_plan_detail" VALUES('BSDS-2025-BA:C15','BSDS-2025-BA','MAJOR-CORE','DSCI410',3,'C',4,'Spring',NULL,NULL,NULL);
INSERT INTO "study_plan_detail" VALUES('BSDS-2025-BA:C16','BSDS-2025-BA','MAJOR-CORE','COMP400',4,'C',4,'Fall',NULL,NULL,NULL);
INSERT INTO "study_plan_detail" VALUES('BSDS-2025-BA:T01','BSDS-2025-BA','MAJOR-CORE','BUSA330',3,'C',3,'Fall',NULL,NULL,NULL);
INSERT INTO "study_plan_detail" VALUES('BSDS-2025-BA:T02','BSDS-2025-BA','MAJOR-CORE','BUSA420',3,'C',4,'Fall',NULL,NULL,NULL);
INSERT INTO "study_plan_detail" VALUES('BSDS-2025-BA:I01','BSDS-2025-BA','INTERN','INTR390',6,'D',4,'Spring',NULL,NULL,90);
INSERT INTO "study_plan_detail" VALUES('MBUS-2025:C01','MBUS-2025','MINOR-CORE','ECON101',3,'D',1,'Fall',NULL,NULL,NULL);
INSERT INTO "study_plan_detail" VALUES('MBUS-2025:C02','MBUS-2025','MINOR-CORE','ACCT201',3,'D',2,'Fall',NULL,NULL,NULL);
INSERT INTO "study_plan_detail" VALUES('MBUS-2025:C03','MBUS-2025','MINOR-CORE','MGMT201',3,'D',2,'Fall',NULL,NULL,NULL);
INSERT INTO "study_plan_detail" VALUES('MCS-2025:C01','MCS-2025','MINOR-CORE','COMP101',3,'D',1,'Fall',NULL,NULL,NULL);
INSERT INTO "study_plan_detail" VALUES('MCS-2025:C02','MCS-2025','MINOR-CORE','COMP201',3,'D',1,'Spring',NULL,NULL,NULL);
INSERT INTO "study_plan_detail" VALUES('MCS-2025:C03','MCS-2025','MINOR-CORE','MATH210',3,'D',2,'Spring',NULL,NULL,NULL);
INSERT INTO "study_plan_detail" VALUES('MDA-2025:C01','MDA-2025','MINOR-CORE','STAT201',3,'D',2,'Fall',NULL,NULL,NULL);
INSERT INTO "study_plan_detail" VALUES('MDA-2025:C02','MDA-2025','MINOR-CORE','COMP101',3,'D',1,'Fall',NULL,NULL,NULL);
INSERT INTO "study_plan_detail" VALUES('MDA-2025:C03','MDA-2025','MINOR-CORE','COMP340',3,'D',3,'Fall',NULL,NULL,NULL);
CREATE TABLE "study_plan_requisite" (
  "line_id" TEXT NOT NULL,
  "kind" TEXT NOT NULL,
  "course_code" TEXT NOT NULL,
  CONSTRAINT "pk_study_plan_requisite" PRIMARY KEY ("line_id", "kind", "course_code")
);
INSERT INTO "study_plan_requisite" VALUES('BBA-2022:G02','prerequisite','ENGL101');
INSERT INTO "study_plan_requisite" VALUES('BBA-2022:C02','prerequisite','ECON101');
INSERT INTO "study_plan_requisite" VALUES('BBA-2022:C08','prerequisite','ACCT201');
INSERT INTO "study_plan_requisite" VALUES('BBA-2022:C09','prerequisite','STAT201');
INSERT INTO "study_plan_requisite" VALUES('BBA-2022:C11','prerequisite','ACCT201');
INSERT INTO "study_plan_requisite" VALUES('BBA-2022:C12','prerequisite','MGMT201');
INSERT INTO "study_plan_requisite" VALUES('BBA-2022:C13','prerequisite','MGMT201');
INSERT INTO "study_plan_requisite" VALUES('BBA-2022:C14','prerequisite','MKTG201');
INSERT INTO "study_plan_requisite" VALUES('BBA-2022:C15','prerequisite','MGMT320');
INSERT INTO "study_plan_requisite" VALUES('BBA-2025:G02','prerequisite','ENGL101');
INSERT INTO "study_plan_requisite" VALUES('BBA-2025:C02','prerequisite','ECON101');
INSERT INTO "study_plan_requisite" VALUES('BBA-2025:C08','prerequisite','ACCT201');
INSERT INTO "study_plan_requisite" VALUES('BBA-2025:C10','prerequisite','ACCT201');
INSERT INTO "study_plan_requisite" VALUES('BBA-2025:C11','prerequisite','MGMT201');
INSERT INTO "study_plan_requisite" VALUES('BBA-2025:C12','prerequisite','MGMT201');
INSERT INTO "study_plan_requisite" VALUES('BBA-2025:C13','prerequisite','MKTG201');
INSERT INTO "study_plan_requisite" VALUES('BBA-2025:C14','prerequisite','FIN301');
INSERT INTO "study_plan_requisite" VALUES('BBA-2025:C15','prerequisite','MGMT310');
INSERT INTO "study_plan_requisite" VALUES('BBA-2025:C16','prerequisite','MGMT320');
INSERT INTO "study_plan_requisite" VALUES('BSBAN-2025:G02','prerequisite','ENGL101');
INSERT INTO "study_plan_requisite" VALUES('BSBAN-2025:C06','prerequisite','MATH101');
INSERT INTO "study_plan_requisite" VALUES('BSBAN-2025:C08','prerequisite','COMP101');
INSERT INTO "study_plan_requisite" VALUES('BSBAN-2025:C08','alternate','CSC101');
INSERT INTO "study_plan_requisite" VALUES('BSBAN-2025:C10','prerequisite','COMP101');
INSERT INTO "study_plan_requisite" VALUES('BSBAN-2025:C11','prerequisite','STAT201');
INSERT INTO "study_plan_requisite" VALUES('BSBAN-2025:C11','alternate','QM210');
INSERT INTO "study_plan_requisite" VALUES('BSBAN-2025:C12','prerequisite','ECON101');
INSERT INTO "study_plan_requisite" VALUES('BSBAN-2025:C13','prerequisite','BUSA210');
INSERT INTO "study_plan_requisite" VALUES('BSBAN-2025:C14','prerequisite','STAT201');
INSERT INTO "study_plan_requisite" VALUES('BSBAN-2025:C15','prerequisite','STAT201');
INSERT INTO "study_plan_requisite" VALUES('BSBAN-2025:C16','prerequisite','DSCI210');
INSERT INTO "study_plan_requisite" VALUES('BSBAN-2025:C17','prerequisite','BUSA330');
INSERT INTO "study_plan_requisite" VALUES('BSCS-2022:G02','prerequisite','ENGL101');
INSERT INTO "study_plan_requisite" VALUES('BSCS-2022:C02','prerequisite','MATH101');
INSERT INTO "study_plan_requisite" VALUES('BSCS-2022:C04','prerequisite','CSC101');
INSERT INTO "study_plan_requisite" VALUES('BSCS-2022:C04','alternate','COMP101');
INSERT INTO "study_plan_requisite" VALUES('BSCS-2022:C05','prerequisite','MATH101');
INSERT INTO "study_plan_requisite" VALUES('BSCS-2022:C06','prerequisite','MATH101');
INSERT INTO "study_plan_requisite" VALUES('BSCS-2022:C07','prerequisite','CSC101');
INSERT INTO "study_plan_requisite" VALUES('BSCS-2022:C07','alternate','COMP101');
INSERT INTO "study_plan_requisite" VALUES('BSCS-2022:C08','prerequisite','CSC201');
INSERT INTO "study_plan_requisite" VALUES('BSCS-2022:C08','alternate','COMP201');
INSERT INTO "study_plan_requisite" VALUES('BSCS-2022:C09','prerequisite','CSC101');
INSERT INTO "study_plan_requisite" VALUES('BSCS-2022:C09','alternate','COMP101');
INSERT INTO "study_plan_requisite" VALUES('BSCS-2022:C10','prerequisite','CSC201');
INSERT INTO "study_plan_requisite" VALUES('BSCS-2022:C10','alternate','COMP201');
INSERT INTO "study_plan_requisite" VALUES('BSCS-2022:C11','prerequisite','CSC201');
INSERT INTO "study_plan_requisite" VALUES('BSCS-2022:C11','alternate','COMP201');
INSERT INTO "study_plan_requisite" VALUES('BSCS-2022:C12','prerequisite','COMP220');
INSERT INTO "study_plan_requisite" VALUES('BSCS-2022:C13','prerequisite','CSC201');
INSERT INTO "study_plan_requisite" VALUES('BSCS-2022:C13','alternate','COMP201');
INSERT INTO "study_plan_requisite" VALUES('BSCS-2022:C14','prerequisite','MATH210');
INSERT INTO "study_plan_requisite" VALUES('BSCS-2022:C15','prerequisite','COMP250');
INSERT INTO "study_plan_requisite" VALUES('BSCS-2022:C15','alternate','COMP251');
INSERT INTO "study_plan_requisite" VALUES('BSCS-2022:C17','prerequisite','COMP301');
INSERT INTO "study_plan_requisite" VALUES('BSCS-2025:G02','prerequisite','ENGL101');
INSERT INTO "study_plan_requisite" VALUES('BSCS-2025:C02','prerequisite','MATH101');
INSERT INTO "study_plan_requisite" VALUES('BSCS-2025:C04','prerequisite','COMP101');
INSERT INTO "study_plan_requisite" VALUES('BSCS-2025:C04','alternate','CSC101');
INSERT INTO "study_plan_requisite" VALUES('BSCS-2025:C05','prerequisite','MATH101');
INSERT INTO "study_plan_requisite" VALUES('BSCS-2025:C06','prerequisite','MATH101');
INSERT INTO "study_plan_requisite" VALUES('BSCS-2025:C07','prerequisite','COMP101');
INSERT INTO "study_plan_requisite" VALUES('BSCS-2025:C07','alternate','CSC101');
INSERT INTO "study_plan_requisite" VALUES('BSCS-2025:C08','prerequisite','COMP201');
INSERT INTO "study_plan_requisite" VALUES('BSCS-2025:C08','alternate','CSC201');
INSERT INTO "study_plan_requisite" VALUES('BSCS-2025:C09','prerequisite','COMP101');
INSERT INTO "study_plan_requisite" VALUES('BSCS-2025:C09','alternate','CSC101');
INSERT INTO "study_plan_requisite" VALUES('BSCS-2025:C10','prerequisite','COMP201');
INSERT INTO "study_plan_requisite" VALUES('BSCS-2025:C10','alternate','CSC201');
INSERT INTO "study_plan_requisite" VALUES('BSCS-2025:C11','prerequisite','COMP201');
INSERT INTO "study_plan_requisite" VALUES('BSCS-2025:C11','alternate','CSC201');
INSERT INTO "study_plan_requisite" VALUES('BSCS-2025:C11','corequisite','COMP251');
INSERT INTO "study_plan_requisite" VALUES('BSCS-2025:C12','prerequisite','COMP201');
INSERT INTO "study_plan_requisite" VALUES('BSCS-2025:C12','alternate','CSC201');
INSERT INTO "study_plan_requisite" VALUES('BSCS-2025:C13','prerequisite','COMP220');
INSERT INTO "study_plan_requisite" VALUES('BSCS-2025:C14','prerequisite','COMP201');
INSERT INTO "study_plan_requisite" VALUES('BSCS-2025:C14','alternate','CSC201');
INSERT INTO "study_plan_requisite" VALUES('BSCS-2025:C15','prerequisite','MATH210');
INSERT INTO "study_plan_requisite" VALUES('BSCS-2025:C16','prerequisite','COMP251');
INSERT INTO "study_plan_requisite" VALUES('BSCS-2025:C16','alternate','COMP250');
INSERT INTO "study_plan_requisite" VALUES('BSCS-2025:C18','prerequisite','COMP301');
INSERT INTO "study_plan_requisite" VALUES('BSCS-2025-AI:G02','prerequisite','ENGL101');
INSERT INTO "study_plan_requisite" VALUES('BSCS-2025-AI:C02','prerequisite','MATH101');
INSERT INTO "study_plan_requisite" VALUES('BSCS-2025-AI:C04','prerequisite','COMP101');
INSERT INTO "study_plan_requisite" VALUES('BSCS-2025-AI:C04','alternate','CSC101');
INSERT INTO "study_plan_requisite" VALUES('BSCS-2025-AI:C05','prerequisite','MATH101');
INSERT INTO "study_plan_requisite" VALUES('BSCS-2025-AI:C06','prerequisite','MATH101');
INSERT INTO "study_plan_requisite" VALUES('BSCS-2025-AI:C07','prerequisite','COMP101');
INSERT INTO "study_plan_requisite" VALUES('BSCS-2025-AI:C07','alternate','CSC101');
INSERT INTO "study_plan_requisite" VALUES('BSCS-2025-AI:C08','prerequisite','COMP201');
INSERT INTO "study_plan_requisite" VALUES('BSCS-2025-AI:C08','alternate','CSC201');
INSERT INTO "study_plan_requisite" VALUES('BSCS-2025-AI:C09','prerequisite','COMP101');
INSERT INTO "study_plan_requisite" VALUES('BSCS-2025-AI:C09','alternate','CSC101');
INSERT INTO "study_plan_requisite" VALUES('BSCS-2025-AI:C10','prerequisite','COMP201');
INSERT INTO "study_plan_requisite" VALUES('BSCS-2025-AI:C10','alternate','CSC201');
INSERT INTO "study_plan_requisite" VALUES('BSCS-2025-AI:C11','prerequisite','COMP201');
INSERT INTO "study_plan_requisite" VALUES('BSCS-2025-AI:C11','alternate','CSC201');
INSERT INTO "study_plan_requisite" VALUES('BSCS-2025-AI:C11','corequisite','COMP251');
INSERT INTO "study_plan_requisite" VALUES('BSCS-2025-AI:C12','prerequisite','COMP201');
INSERT INTO "study_plan_requisite" VALUES('BSCS-2025-AI:C12','alternate','CSC201');
INSERT INTO "study_plan_requisite" VALUES('BSCS-2025-AI:C13','prerequisite','COMP220');
INSERT INTO "study_plan_requisite" VALUES('BSCS-2025-AI:C14','prerequisite','COMP201');
INSERT INTO "study_plan_requisite" VALUES('BSCS-2025-AI:C14','alternate','CSC201');
INSERT INTO "study_plan_requisite" VALUES('BSCS-2025-AI:C15','prerequisite','MATH210');
INSERT INTO "study_plan_requisite" VALUES('BSCS-2025-AI:C16','prerequisite','COMP251');
INSERT INTO "study_plan_requisite" VALUES('BSCS-2025-AI:C16','alternate','COMP250');
INSERT INTO "study_plan_requisite" VALUES('BSCS-2025-AI:C18','prerequisite','COMP301');
INSERT INTO "study_plan_requisite" VALUES('BSCS-2025-AI:T01','prerequisite','COMP301');
INSERT INTO "study_plan_requisite" VALUES('BSCS-2025-AI:T02','prerequisite','STAT201');
INSERT INTO "study_plan_requisite" VALUES('BSDS-2022:G02','prerequisite','ENGL101');
INSERT INTO "study_plan_requisite" VALUES('BSDS-2022:C02','prerequisite','MATH101');
INSERT INTO "study_plan_requisite" VALUES('BSDS-2022:C04','prerequisite','CSC101');
INSERT INTO "study_plan_requisite" VALUES('BSDS-2022:C04','alternate','COMP101');
INSERT INTO "study_plan_requisite" VALUES('BSDS-2022:C05','prerequisite','MATH101');
INSERT INTO "study_plan_requisite" VALUES('BSDS-2022:C06','prerequisite','MATH101');
INSERT INTO "study_plan_requisite" VALUES('BSDS-2022:C07','prerequisite','CSC101');
INSERT INTO "study_plan_requisite" VALUES('BSDS-2022:C07','alternate','COMP101');
INSERT INTO "study_plan_requisite" VALUES('BSDS-2022:C08','prerequisite','DSCI200');
INSERT INTO "study_plan_requisite" VALUES('BSDS-2022:C09','prerequisite','CSC201');
INSERT INTO "study_plan_requisite" VALUES('BSDS-2022:C09','alternate','COMP201');
INSERT INTO "study_plan_requisite" VALUES('BSDS-2022:C10','prerequisite','STAT201');
INSERT INTO "study_plan_requisite" VALUES('BSDS-2022:C11','prerequisite','CSC201');
INSERT INTO "study_plan_requisite" VALUES('BSDS-2022:C11','alternate','COMP201');
INSERT INTO "study_plan_requisite" VALUES('BSDS-2022:C12','prerequisite','STAT201');
INSERT INTO "study_plan_requisite" VALUES('BSDS-2022:C13','prerequisite','STAT201');
INSERT INTO "study_plan_requisite" VALUES('BSDS-2022:C14','prerequisite','DSCI230');
INSERT INTO "study_plan_requisite" VALUES('BSDS-2022:C15','prerequisite','DSCI320');
INSERT INTO "study_plan_requisite" VALUES('BSDS-2022:C16','prerequisite','DSCI330');
INSERT INTO "study_plan_requisite" VALUES('BSDS-2025:G02','prerequisite','ENGL101');
INSERT INTO "study_plan_requisite" VALUES('BSDS-2025:C02','prerequisite','MATH101');
INSERT INTO "study_plan_requisite" VALUES('BSDS-2025:C04','prerequisite','COMP101');
INSERT INTO "study_plan_requisite" VALUES('BSDS-2025:C04','alternate','CSC101');
INSERT INTO "study_plan_requisite" VALUES('BSDS-2025:C05','prerequisite','MATH101');
INSERT INTO "study_plan_requisite" VALUES('BSDS-2025:C06','prerequisite','MATH101');
INSERT INTO "study_plan_requisite" VALUES('BSDS-2025:C07','prerequisite','COMP101');
INSERT INTO "study_plan_requisite" VALUES('BSDS-2025:C07','alternate','CSC101');
INSERT INTO "study_plan_requisite" VALUES('BSDS-2025:C08','prerequisite','COMP201');
INSERT INTO "study_plan_requisite" VALUES('BSDS-2025:C08','alternate','CSC201');
INSERT INTO "study_plan_requisite" VALUES('BSDS-2025:C09','prerequisite','STAT201');
INSERT INTO "study_plan_requisite" VALUES('BSDS-2025:C10','prerequisite','COMP201');
INSERT INTO "study_plan_requisite" VALUES('BSDS-2025:C10','alternate','CSC201');
INSERT INTO "study_plan_requisite" VALUES('BSDS-2025:C11','prerequisite','STAT201');
INSERT INTO "study_plan_requisite" VALUES('BSDS-2025:C12','prerequisite','STAT201');
INSERT INTO "study_plan_requisite" VALUES('BSDS-2025:C13','prerequisite','DSCI230');
INSERT INTO "study_plan_requisite" VALUES('BSDS-2025:C15','prerequisite','DSCI320');
INSERT INTO "study_plan_requisite" VALUES('BSDS-2025:C16','prerequisite','DSCI330');
INSERT INTO "study_plan_requisite" VALUES('BSDS-2025-BA:G02','prerequisite','ENGL101');
INSERT INTO "study_plan_requisite" VALUES('BSDS-2025-BA:C02','prerequisite','MATH101');
INSERT INTO "study_plan_requisite" VALUES('BSDS-2025-BA:C04','prerequisite','COMP101');
INSERT INTO "study_plan_requisite" VALUES('BSDS-2025-BA:C04','alternate','CSC101');
INSERT INTO "study_plan_requisite" VALUES('BSDS-2025-BA:C05','prerequisite','MATH101');
INSERT INTO "study_plan_requisite" VALUES('BSDS-2025-BA:C06','prerequisite','MATH101');
INSERT INTO "study_plan_requisite" VALUES('BSDS-2025-BA:C07','prerequisite','COMP101');
INSERT INTO "study_plan_requisite" VALUES('BSDS-2025-BA:C07','alternate','CSC101');
INSERT INTO "study_plan_requisite" VALUES('BSDS-2025-BA:C08','prerequisite','COMP201');
INSERT INTO "study_plan_requisite" VALUES('BSDS-2025-BA:C08','alternate','CSC201');
INSERT INTO "study_plan_requisite" VALUES('BSDS-2025-BA:C09','prerequisite','STAT201');
INSERT INTO "study_plan_requisite" VALUES('BSDS-2025-BA:C10','prerequisite','COMP201');
INSERT INTO "study_plan_requisite" VALUES('BSDS-2025-BA:C10','alternate','CSC201');
INSERT INTO "study_plan_requisite" VALUES('BSDS-2025-BA:C11','prerequisite','STAT201');
INSERT INTO "study_plan_requisite" VALUES('BSDS-2025-BA:C12','prerequisite','STAT201');
INSERT INTO "study_plan_requisite" VALUES('BSDS-2025-BA:C13','prerequisite','DSCI230');
INSERT INTO "study_plan_requisite" VALUES('BSDS-2025-BA:C15','prerequisite','DSCI320');
INSERT INTO "study_plan_requisite" VALUES('BSDS-2025-BA:C16','prerequisite','DSCI330');
INSERT INTO "study_plan_requisite" VALUES('BSDS-2025-BA:T01','prerequisite','STAT201');
INSERT INTO "study_plan_requisite" VALUES('BSDS-2025-BA:T02','prerequisite','BUSA330');
INSERT INTO "study_plan_requisite" VALUES('MCS-2025:C02','prerequisite','COMP101');
INSERT INTO "study_plan_requisite" VALUES('MCS-2025:C02','alternate','CSC101');
INSERT INTO "study_plan_requisite" VALUES('MDA-2025:C03','prerequisite','COMP101');
INSERT INTO "study_plan_requisite" VALUES('MDA-2025:C03','alternate','CSC101');
CREATE TABLE "study_plan_version" (
  "version_id" TEXT NOT NULL,
  "program_code" TEXT NOT NULL,
  "catalog_year" INTEGER NOT NULL,
  "version_no" INTEGER NOT NULL,
  "status" TEXT NOT NULL,
  "effective_term" TEXT NOT NULL,
  "intake_closed_after" TEXT,
  "approval_ref" TEXT NOT NULL,
  "supersedes" TEXT,
  CONSTRAINT "pk_study_plan_version" PRIMARY KEY ("version_id")
);
INSERT INTO "study_plan_version" VALUES('BBA-2022','BBA',2022,1,'closed_to_intake','2022FA','2025SU','SA-2022-05',NULL);
INSERT INTO "study_plan_version" VALUES('BBA-2025','BBA',2025,2,'active','2025FA',NULL,'SA-2025-03','BBA-2022');
INSERT INTO "study_plan_version" VALUES('BSBAN-2025','BSBAN',2025,1,'active','2025FA',NULL,'SA-2025-05',NULL);
INSERT INTO "study_plan_version" VALUES('BSCS-2022','BSCS',2022,1,'closed_to_intake','2022FA','2025SU','SA-2022-03',NULL);
INSERT INTO "study_plan_version" VALUES('BSCS-2025','BSCS',2025,2,'active','2025FA',NULL,'SA-2025-01','BSCS-2022');
INSERT INTO "study_plan_version" VALUES('BSCS-2025-AI','BSCS',2025,1,'active','2025FA',NULL,'SA-2025-12',NULL);
INSERT INTO "study_plan_version" VALUES('BSDS-2022','BSDS',2022,1,'closed_to_intake','2022FA','2025SU','SA-2022-04',NULL);
INSERT INTO "study_plan_version" VALUES('BSDS-2025','BSDS',2025,2,'active','2025FA',NULL,'SA-2025-02','BSDS-2022');
INSERT INTO "study_plan_version" VALUES('BSDS-2025-BA','BSDS',2025,1,'active','2025FA',NULL,'SA-2025-13',NULL);
INSERT INTO "study_plan_version" VALUES('MBUS-2025','MBUS',2025,1,'active','2025FA',NULL,'SA-2025-08',NULL);
INSERT INTO "study_plan_version" VALUES('MCS-2025','MCS',2025,1,'active','2025FA',NULL,'SA-2025-07',NULL);
INSERT INTO "study_plan_version" VALUES('MDA-2025','MDA',2025,1,'active','2025FA',NULL,'SA-2025-06',NULL);
CREATE TABLE "term" (
  "term_code" TEXT NOT NULL,
  "name" TEXT NOT NULL,
  "start_date" TEXT NOT NULL,
  "end_date" TEXT NOT NULL,
  "seq" INTEGER NOT NULL,
  CONSTRAINT "pk_term" PRIMARY KEY ("term_code")
);
INSERT INTO "term" VALUES('2022FA','Fall 2022','2022-08-29','2022-12-20',1);
INSERT INTO "term" VALUES('2023SP','Spring 2023','2023-01-16','2023-05-31',2);
INSERT INTO "term" VALUES('2023SU','Summer 2023','2023-06-12','2023-08-10',3);
INSERT INTO "term" VALUES('2023FA','Fall 2023','2023-08-28','2023-12-19',4);
INSERT INTO "term" VALUES('2024SP','Spring 2024','2024-01-15','2024-05-31',5);
INSERT INTO "term" VALUES('2024SU','Summer 2024','2024-06-10','2024-08-08',6);
INSERT INTO "term" VALUES('2024FA','Fall 2024','2024-08-26','2024-12-18',7);
INSERT INTO "term" VALUES('2025SP','Spring 2025','2025-01-13','2025-05-31',8);
INSERT INTO "term" VALUES('2025SU','Summer 2025','2025-06-09','2025-08-07',9);
INSERT INTO "term" VALUES('2025FA','Fall 2025','2025-08-25','2025-12-17',10);
INSERT INTO "term" VALUES('2026SP','Spring 2026','2026-01-12','2026-05-31',11);
INSERT INTO "term" VALUES('2026SU','Summer 2026','2026-06-08','2026-08-06',12);
INSERT INTO "term" VALUES('2026FA','Fall 2026','2026-08-24','2026-12-16',13);
INSERT INTO "term" VALUES('2027SP','Spring 2027','2027-01-11','2027-05-31',14);
CREATE TABLE "transfer_credit" (
  "transfer_id" INTEGER NOT NULL,
  "student_id" TEXT NOT NULL,
  "term_code" TEXT NOT NULL,
  "source" TEXT NOT NULL,
  "external_code" TEXT NOT NULL,
  "mapped_code" TEXT,
  "credits" INTEGER NOT NULL,
  "grade" TEXT,
  "origin" TEXT NOT NULL,
  "approval_ref" TEXT,
  CONSTRAINT "pk_transfer_credit" PRIMARY KEY ("transfer_id")
);
COMMIT;
