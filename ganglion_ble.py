import bglib
from serial import Serial
import time
import struct
import datetime

# service for communication, as per docs
BLE_SERVICE = "fe84"

# characteristics of interest
BLE_CHAR_RECEIVE = "2d30c082f39f4ce6923f3484ea480596"
BLE_CHAR_SEND = "2d30c083f39f4ce6923f3484ea480596"
BLE_CHAR_DISCONNECT = "2d30c084f39f4ce6923f3484ea480596"

STATE_STANDBY = 0
STATE_CONNECTING = 1
STATE_FINDING_SERVICES = 2
STATE_FINDING_ATTRIBUTES = 3
STATE_LISTENING_MEASUREMENTS = 4



class Ganglion():
    def __init__(self, port=None, baud_rate=115200, mac_addrs='E65D54F2F438'):
        self.port = port
        self.baud_rate = baud_rate
        self.board = bglib.BGLib()
        self.mac = mac_addrs

        self.state = STATE_STANDBY
        self.connect_to = False



        # BGAPI Events responses
        def my_ble_evt_gap_scan_response(sender, args):
            uuid_gang_service = [0xfe, 0x84]

            # pull all advertised service info from ad packet
            ad_services = []
            this_field = []
            bytes_left = 0
            for b in args['data']:
                if bytes_left == 0:
                    bytes_left = b
                    this_field = []
                else:
                    this_field.append(b)
                    bytes_left = bytes_left - 1
                    if bytes_left == 0:
                        if this_field[0] == 0x02 or this_field[0] == 0x03: # partial or complete list of 16-bit UUIDs
                            for i in xrange((len(this_field) - 1) / 2):
                                ad_services.append(this_field[-1 - i*2 : -3 - i*2 : -1])
                        if this_field[0] == 0x04 or this_field[0] == 0x05: # partial or complete list of 32-bit UUIDs
                            for i in xrange((len(this_field) - 1) / 4):
                                ad_services.append(this_field[-1 - i*4 : -5 - i*4 : -1])
                        if this_field[0] == 0x06 or this_field[0] == 0x07: # partial or complete list of 128-bit UUIDs
                            for i in xrange((len(this_field) - 1) / 16):
                                ad_services.append(this_field[-1 - i*16 : -17 - i*16 : -1])

            # check for 0x180A (official heart rate service UUID)
            if self.connect_to and ''.join(['%02X' % b for b in args["sender"][::-1]]) == self.mac:
                print('Ganglion found! Trying to Connect...')
                self.board.send_command(self.ser, self.board.ble_cmd_gap_connect_direct(args['sender'], args['address_type'], 0x20, 0x30, 0x100, 0))
                print('Connected!')
                self.board.check_activity(self.ser, 1)
                self.state = STATE_CONNECTING




        # add handlers for BGAPI events
        self.board.ble_evt_gap_scan_response += my_ble_evt_gap_scan_response
        # self.board.ble_evt_connection_status += my_ble_evt_connection_status
        # self.board.ble_evt_attclient_group_found += my_ble_evt_attclient_group_found
        # self.board.ble_evt_attclient_find_information_found += my_ble_evt_attclient_find_information_found
        # self.board.ble_evt_attclient_procedure_completed += my_ble_evt_attclient_procedure_completed
        # self.board.ble_evt_attclient_attribute_value += my_ble_evt_attclient_attribute_value


    def connect(self):
        # create serial port object
        try:
            self.ser = Serial(port=self.port, baudrate=self.baud_rate, timeout=1, writeTimeout=1)
        except serial.SerialException as e:
            print "\n================================================================"
            print "Port error (name='%s', baud='%ld'): %s" % (self.port, self.baud_rate, e)
            print "================================================================"
            exit(2)

        # flush buffers
        self.ser.flushInput()
        self.ser.flushOutput()

        # disconnect if we are connected already
        self.board.send_command(self.ser, self.board.ble_cmd_connection_disconnect(0))
        self.board.check_activity(self.ser, 1)

        # stop advertising if we are advertising already
        self.board.send_command(self.ser, self.board.ble_cmd_gap_set_mode(0, 0))
        self.board.check_activity(self.ser, 1)

        # stop scanning if we are scanning already
        self.board.send_command(self.ser, self.board.ble_cmd_gap_end_procedure())
        self.board.check_activity(self.ser, 1)

        # set scan parameters
        self.board.send_command(self.ser, self.board.ble_cmd_gap_set_scan_parameters(0xC8, 0xC8, 0))
        self.board.check_activity(self.ser, 1)

        # start scanning now
        print "Scanning for Ganglions..."
        self.connect_to = True
        self.board.send_command(self.ser, self.board.ble_cmd_gap_discover(2))
        self.board.check_activity(self.ser, 1)

        while not self.state:
            # check for all incoming data (no timeout, non-blocking)
            self.board.check_activity(self.ser)

            # don't burden the CPU
            time.sleep(0.01)

    def send_command(self, char):
        pass

    def start_stream(self):
        pass

    def stop_stream(self):
        pass

    def disconnect(self):
        print("Disconnecting...")
        self.board.send_command(self.ser, self.board.ble_cmd_connection_disconnect(0))
        self.board.check_activity(self.ser, 1)

        # stop advertising if we are advertising already
        self.board.send_command(self.ser, self.board.ble_cmd_gap_set_mode(0, 0))
        self.board.check_activity(self.ser, 1)

        # stop scanning if we are scanning already
        self.board.send_command(self.ser, self.board.ble_cmd_gap_end_procedure())
        self.board.check_activity(self.ser, 1)

        print('Bye')


board = Ganglion(port='COM6')
board.connect()
time.sleep(1)
board.disconnect()
