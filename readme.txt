测试：python3 -m unittest discover -s tests/certification_cycles -p 'test_*.py'
构建：python3 -m compileall -q regional_brand_guard tests/certification_cycles
验收：python3 -m regional_brand_guard.certification.cli fixtures/demo.json --database /tmp/regional_brand_guard-acceptance.sqlite3
所有输入均为本地合成数据，运行不需要网络。
