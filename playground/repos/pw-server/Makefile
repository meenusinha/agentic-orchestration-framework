CXX ?= g++
CC  ?= gcc
CXXFLAGS := -Iinclude -Igen -Isrc
SRCS := src/server.cpp src/session_notifier.cpp src/main.cpp src/db_config.cpp
OBJS := $(SRCS:.cpp=.o)
POLICY_SRCS := $(wildcard policy/*.c)
POLICY_OBJS := $(patsubst %.c,%.o,$(POLICY_SRCS))

all: authd

libpwdpolicy.a: $(POLICY_OBJS)
	$(AR) rcs $@ $^

authd: $(OBJS) libpwdpolicy.a
	$(CXX) -o $@ $(OBJS) -L. -lpwdpolicy -lTAO -lTAO_PortableServer

run_tests: tests/test_policy.o libpwdpolicy.a
	$(CXX) -o $@ $^
