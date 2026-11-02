# sample-server — test fixture

A small sample server process (`authd`) used by the automated tests: IDL interfaces,
a C++ servant, a pure-C policy library, generated stubs, a Makefile and a compile
database, tests, vendored code and docs. `src/db_config.cpp` contains **fake**
credentials so the tests can verify that secrets are redacted.
