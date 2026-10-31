package Deploy;
use strict;
use warnings;

# Restart the UI processes on a host after an update.
sub restart_ui {
    my ($host) = @_;
    system("ssh", $host, "systemctl", "restart", "ui-shell");
}

1;
