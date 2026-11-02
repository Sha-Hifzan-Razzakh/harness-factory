---
name: Editing configuration and text files
description: Read the whole file, change only what was asked, keep its format, respect layered configs, and re-read after writing.
---
1. Read the whole file before changing it. Write the file back in full: keep every line you were not asked to
   change, with its order, spacing and comments.
2. Layered configuration: a base file holds defaults and each environment file overrides some of them. To know an
   environment's real value, read its own file first, then the base for anything it does not set. Change the
   file the task is about, not the shared base, unless told to.
3. When the right value is documented elsewhere (docs, another config), look it up instead of guessing.
4. JSON: change the one value, keep the structure, and make sure the result still parses. Booleans are true and
   false, unquoted.
5. Text with an established format (changelogs, runbooks): copy the existing pattern for headings, dates and
   bullet style. New release entries go above older ones.
6. A version number that is the same as the project's may belong to something else (a pinned dependency). Change
   only what declares the project itself.
7. Re-read the file after writing it and compare it with the task.
