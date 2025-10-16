from pathlib import Path
text = Path("views.py").read_text()
old = "            name = inf.name or \"-\"\n\n            last_raw = inf.last_action_relative or inf.last_action_status or \"\"\n\n            last = last_raw or \"-\"\n\n\n\n            it_name = QTableWidgetItem(name)\n\n            it_id = QTableWidgetItem(str(inf.user_id))\n\n            it_level = QTableWidgetItem(lvl)\n\n            it_last = QTableWidgetItem(last)\n"
new = "            name = inf.name or \"-\"\n            last_raw = inf.last_action_relative or inf.last_action_status or \"\"\n            last = last_raw or \"-\"\n            lvl = \"\" if inf.level is None else str(inf.level)\n\n            it_name = QTableWidgetItem(name)\n            it_id = QTableWidgetItem(str(inf.user_id))\n            it_level = QTableWidgetItem(lvl)\n            it_last = QTableWidgetItem(last)\n"
if old not in text:
    raise SystemExit('pattern not found for lvl block')
Path("views.py").write_text(text.replace(old, new))
