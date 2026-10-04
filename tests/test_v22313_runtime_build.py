import os,sys,pathlib

ROOT=pathlib.Path(__file__).resolve().parents[1]

def test_release_uses_python_311():
    s=(ROOT/".github/workflows/release.yml").read_text(encoding="utf-8")
    assert "python-version: '3.11'" in s
    assert "python-version: '3.12'" not in s

def test_release_bundles_vc_runtime():
    s=(ROOT/".github/workflows/release.yml").read_text(encoding="utf-8")
    assert "vcruntime140.dll" in s
    assert "--add-binary" in s
    assert "pyi-archive_viewer" in s
    assert "python311\\.dll" in s or "python311.dll" in s

if __name__=="__main__":
    for t in [test_release_uses_python_311,test_release_bundles_vc_runtime]:
        t(); print("PASS",t.__name__)
