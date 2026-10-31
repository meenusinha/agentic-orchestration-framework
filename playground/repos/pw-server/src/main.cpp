#include "server.h"

int main(int argc, char** argv) {
  CORBA::ORB_var orb = CORBA::ORB_init(argc, argv);
  authd::PasswordServiceImpl servant;
  register_service(orb, "AuthService", &servant);
  orb->run();
  return 0;
}
