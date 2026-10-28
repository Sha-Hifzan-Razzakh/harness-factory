PLAN = [
    {"tool": "fs.read_file", "args": {"path": "config/app.ini"}},
    {"tool": "fs.write_file", "args": {"path": "config/app.ini",
                                       "content": "[server]\nhost = 0.0.0.0\nport = 9090\n\n[logging]\nlevel = info\n"}},
]
FINAL = "Changed the port in config/app.ini to 9090."
