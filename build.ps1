# Builds ZeroCompanyOperatorShare.exe into dist\
python -m PyInstaller --onefile --windowed --name "ZeroCompanyOperatorShare" -y zc_operators.py
if ($?) {
    $exe = "dist\ZeroCompanyOperatorShare.exe"
    "Built $exe"
    "SHA256: " + (Get-FileHash $exe -Algorithm SHA256).Hash
}
