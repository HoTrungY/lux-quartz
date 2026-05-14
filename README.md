Terminal 1 (BE):

cd "c:\My Web Sites\clonelandingpage"
.\.venv313\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python scripts/preprocess_us_catalog.py
python -m uvicorn app.main:app --reload --port 8787
Nếu bị chặn activate script:

Set-ExecutionPolicy -Scope Process Bypass
rồi chạy lại lệnh activate.

Kiểm tra BE sống:

http://127.0.0.1:8787/api/health
Terminal 2 (FE):

Cách 1 (đơn giản):

cd "c:\My Web Sites\clonelandingpage"
python -m http.server 5501
Mở:

http://127.0.0.1:5501/luxquartzvietnam.com/en
