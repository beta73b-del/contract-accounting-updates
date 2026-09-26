# PyInstaller hook: pyspellchecker хранит языковые словари как data-файлы.
from PyInstaller.utils.hooks import collect_data_files

datas = collect_data_files("spellchecker")