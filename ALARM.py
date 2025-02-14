from datetime import datetime
from django.db.models import Q
import pytz

tehran_tz = pytz.timezone('Asia/Tehran')
pin_states = {}

def handle_alarm_severity(alarm):
    """
    Handles actions based on the severity of the alarm.
    """
    if alarm.severity == "high":
        print(f"Critical action required for alarm: {alarm.alaram_details}")
        #sms
    elif alarm.severity == "medium":
        print(f"Moderate action required for alarm: {alarm.alaram_details}")
        #email
    elif alarm.severity == "low":
        print(f"Low-priority action for alarm: {alarm.alaram_details}")
        #nothing
    else:
        print(f"Unknown severity level: {alarm.severity}")


def update_pin_state(device_id, pin, state):
    device = Device.objects.get(id=device_id)
    pin_state, created = PinState.objects.get_or_create(device=device, pin=pin)
    pin_state.state = state
    pin_state.save()

def get_pin_state(device_id, pin):
    try:
        device = Device.objects.get(id=device_id)
        pin_state = PinState.objects.get(device=device, pin=pin)
        return pin_state.state
    except PinState.DoesNotExist:
        return "unknown"

def clear_pin_state(device_id, pin=None):
    device = Device.objects.get(id=device_id)
    if pin is None:
        PinState.objects.filter(device=device).delete()
    else:
        PinState.objects.filter(device=device, pin=pin).delete()

def print_pin_states():
    for device_id, pins in pin_states.items():
        print(f"Device {device_id}: {pins}")

def get_current_tehran_time():
    current_datetime = datetime.now(tehran_tz)
    current_time = current_datetime.time()
    current_date = current_datetime.date()
    return current_datetime, current_time, current_date

def is_in_time_window(start_time, stop_time):
    _, now, _ = get_current_tehran_time()
    start = datetime.strptime(start_time, "%H:%M").time()
    stop = datetime.strptime(stop_time, "%H:%M").time()

    if start <= stop:
        return start <= now <= stop
    return now >= start or now <= stop

def check_condition(condition_rule, value):
    try:
        return eval(condition_rule.replace("x", str(value)))
    except Exception as e:
        print(f"Error in condition evaluation: {e}")
        return False

def parse_pins(pins_str):
    return [int(pin.strip()) for pin in pins_str.split("&") if pin.strip().isdigit()]

def send_pin_command(client, serial_number, pin_number, status_pin):
    method = "setState"
    params = {"pin": pin_number, "state": status_pin}

    ack_received = send_command(client, serial_number, method, params, pin_number)
if ack_received:
    update_pin_state(serial_number, pin_number, status_pin)
return ack_received


def rollback_pins(client, serial_number, pins, rule):
    for pin in pins:
        current_state = get_pin_state(serial_number, pin)
    if current_state in ["on", "off"]:
        new_state = "off" if current_state == "on" else "on"
        ack = send_pin_command(client, serial_number, pin, new_state)
        if ack:
            update_pin_state(serial_number, pin, new_state)
            print(f"Rollback successful for pin {pin} of rule {rule.id}: {current_state} -> {new_state}.")
        else:
            print(f"Failed to rollback pin {pin} of rule {rule.id}.")

def check_and_update_iter_duration(rule):
    now, _, _ = get_current_tehran_time()

    if not rule.date_last_data:
        rule.date_last_data = now
        rule.iter_duration = 0
        rule.save()
        return False

    elapsed_time = (now - rule.date_last_data).total_seconds() / 60

    if elapsed_time <= 2:
        rule.iter_duration += 1
        rule.date_last_data = now
        rule.save()
    else:
        rule.iter_duration = 1
        rule.date_last_data = now

    if rule.iter_duration >= rule.duration:
        rule.iter_duration = 0
        rule.save()
        return True

    return False

def resolve_active_alarms(rule, device, sensor):
    active_alarms = Alarm.objects.filter(rule=rule, device=device, sensor=sensor, status="active")
    for alarm in active_alarms:
        alarm.status = "resolved"
        alarm.resolved_at = datetime.now(tehran_tz)
        alarm.save()



#######################################################################
def process_incoming_data(device, sensor, json_value, client):
  
    rules = RuleChain.objects.filter(device=device, device_sensor=sensor)
    if not rules.exists():
        print(f"No rules found for device {device.serial_number} and sensor {sensor.id}")
        return

    for rule in rules:
        # بررسی وضعیت زمان قانون
        if is_in_time_window(rule.start_time, rule.stop_time):
            if check_condition(rule.condition_rule, json_value):
                if check_and_update_iter_duration(rule):
                    # بررسی وجود آلارم مشابه
                    existing_alarm = Alarm.objects.filter(
                        user_device=device,
                        device_sensor=sensor,
                        triggered_by="rule",
                        alaram_details=f"Rule {rule.id} triggered",
                        severity=rule.severity,
                        is_read=False,
                    ).exists()

                    if not existing_alarm:
                        # ایجاد آلارم جدید
                        alarm = Alarm.objects.create(
                            user_device=device,
                            device_sensor=sensor,
                            triggered_by="rule",
                            alaram_details=f"Rule {rule.id} triggered",
                            severity=rule.severity,
                            triggered_at=datetime.now(tehran_tz),
                            is_read=False,
                        )
                        print(f"New alarm created for rule {rule.id} on device {device.serial_number}.")
                        handle_alarm_severity(alarm)
                    else:
                        print(f"Active alarm already exists for rule {rule.id} on device {device.serial_number}.")

                    # ارسال دستور به پین‌ها
                    pins = parse_pins(rule.pins)
                    for pin in pins:
                        ack = send_pin_command(client, device.serial_number, pin, "on" if rule.status == "on" else "off")
                        if not ack:
                            print(f"Failed to send command to pin {pin}.")
            else:
                # بازگردانی پین‌ها به وضعیت اولیه در صورت عدم تطابق شرط
                pins = parse_pins(rule.pins)
                rollback_pins(client, device.serial_number, pins, rule)
                resolve_active_alarms(device, sensor)
        else:
            # بازگردانی پین‌ها به وضعیت اولیه در صورت اتمام زمان قانون
            pins = parse_pins(rule.pins)
            rollback_pins(client, device.serial_number, pins, rule)
            resolve_active_alarms(device, sensor)
            print(f"Time window ended for rule {rule.id}. Pins rolled back.")

    print_pin_states()
