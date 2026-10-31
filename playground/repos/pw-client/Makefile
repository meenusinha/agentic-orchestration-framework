CXX ?= g++
ui-shell: src/login_dialog.o src/session_handler.o src/legacy_client.o
	$(CXX) -o $@ $^ -lTAO -lauth_stubs
