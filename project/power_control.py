from relay import cut_main_power, restore_main_power, stop_backup, switch_to_backup


FAULTS_REQUIRING_ISOLATION = {
    "OVERLOAD",
    "DISCONNECT",
    "UNDERVOLTAGE",
    "OVERVOLTAGE",
}


class PowerController:
    def __init__(self):
        self.is_isolated = False

    def handle_fault(self, fault_type):
        should_isolate = fault_type in FAULTS_REQUIRING_ISOLATION

        if should_isolate and not self.is_isolated:
            cut_main_power()
            switch_to_backup()
            self.is_isolated = True
        elif not should_isolate and self.is_isolated:
            restore_main_power()
            stop_backup()
            self.is_isolated = False


_default_controller = PowerController()


def handle_fault(fault_type):
    _default_controller.handle_fault(fault_type)
