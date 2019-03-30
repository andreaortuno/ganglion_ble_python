import bglib
from serial import Serial
import time
import struct
import datetime
import sys
from pynput import keyboard

# service for communication, as per docs
BLE_SERVICE = [0x18, 0x00]

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
        self.board.debug = False

        self.state = STATE_STANDBY
        self.connect_to = False
        self.disconnecting = False
        self.received_found = False

        # create serial port object
        try:
            self.ser = Serial(port=self.port, baudrate=self.baud_rate, timeout=1, writeTimeout=1)
        except serial.SerialException as e:
            print "\n================================================================"
            print "Port error (name='%s', baud='%ld'): %s" % (self.port, self.baud_rate, e)
            print "================================================================"
            exit(2)



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

            # connect to Ganglion based on MAC address
            if self.connect_to and ''.join(['%02X' % b for b in args["sender"][::-1]]) == self.mac:
                print('Ganglion found! Trying to Connect...')
                self.board.send_command(self.ser, self.board.ble_cmd_gap_connect_direct(args['sender'], args['address_type'], 0x20, 0x30, 0x100, 0))
                self.board.check_activity(self.ser, 1)
                self.state = STATE_CONNECTING
                self.connect_to = False

        # connection_status handler
        def my_ble_evt_connection_status(sender, args):

            if (args['flags'] & 0x05) == 0x05:
                # connected, now perform service discovery
                print "Connected to %s" % ':'.join(['%02X' % b for b in args['address'][::-1]])
                self.connection_handle = args['connection']
                # self.board.send_command(self.ser, self.board.ble_cmd_attclient_read_by_group_type(args['connection'], 0x0001, 0xFFFF, list(reversed(BLE_SERVICE))))
                self.board.send_command(self.ser, self.board.ble_cmd_attclient_find_information(args['connection'], 0x0001, 0xFFFF))
                self.board.check_activity(self.ser, 1)
                self.state = STATE_FINDING_ATTRIBUTES

        # attclient_group_found handler
        def ble_evt_attclient_find_information_found(sender, args):

            # found "service" attribute groups (UUID=0x2800), check for heart rate service
            # for arg in args:
            #     print(arg)
            #     print(args[arg])
            # if '2A00' == ''.join(['%02X' % b for b in args['uuid'][::-1]]):
            #     self.name_handle = args['chrhandle']
            print(''.join(['%02X' % b for b in args['uuid'][::-1]]))
            print(self.received_found)
            if BLE_CHAR_RECEIVE.upper() == ''.join(['%02X' % b for b in args['uuid'][::-1]]):
                print('Receive characteristic found!')
                self.received_found = True
                self.receive_handle = args['chrhandle']

            elif '2902' == ''.join(['%02X' % b for b in args['uuid'][::-1]]) and self.received_found:
                print('Receive cccs characteristic found!')
                self.receive_handle_ccc = args['chrhandle']
                self.received_found = False

            elif BLE_CHAR_SEND.upper() == ''.join(['%02X' % b for b in args['uuid'][::-1]]):
                print('Send characteristic found!')
                self.send_handle = args['chrhandle']

            elif BLE_CHAR_DISCONNECT.upper() == ''.join(['%02X' % b for b in args['uuid'][::-1]]):
                print('Disconnect characteristic found!')
                self.disconnect_handle = args['chrhandle']

        # attclient_procedure_completed handler
        def my_ble_evt_attclient_procedure_completed(sender, args):
            # check if we just finished searching for attributes
            if self.state == STATE_FINDING_ATTRIBUTES:


                if self.send_handle > 0:
                    print "Writting to send characteristic"

                    # found the measurement + client characteristic configuration, so enable notifications
                    # (this is done by writing 0x01 to the client characteristic configuration attribute)
                    self.state = STATE_LISTENING_MEASUREMENTS
                    self.board.send_command(self.ser, self.board.ble_cmd_attclient_attribute_write(self.connection_handle, self.receive_handle_ccc, [0x01, 0x00]))
                    self.board.send_command(self.ser, self.board.ble_cmd_attclient_attribute_write(self.connection_handle, self.send_handle, [0x62, 0x00]))
                    self.board.check_activity(self.ser, 1)
                else:
                    print "Could not find send attribute"

        # attclient_attribute_value handler
        def my_ble_evt_attclient_attribute_value(sender, args):

            # check for a new value from the connected peripheral's heart rate measurement attribute
            if args['connection'] == self.connection_handle and args['atthandle'] == self.receive_handle:
                print(args['value'])

        def my_ble_rsp_attclient_read_long(sender, args):
            for arg in args:
                print(arg)
                print(args[arg])


        # add handlers for BGAPI events
        self.board.ble_evt_gap_scan_response += my_ble_evt_gap_scan_response
        self.board.ble_evt_connection_status += my_ble_evt_connection_status
        self.board.ble_evt_attclient_find_information_found += ble_evt_attclient_find_information_found
        self.board.ble_evt_attclient_procedure_completed += my_ble_evt_attclient_procedure_completed
        self.board.ble_evt_attclient_attribute_value += my_ble_evt_attclient_attribute_value
        self.board.ble_rsp_attclient_read_long += my_ble_rsp_attclient_read_long


    def connect(self):

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


        while not self.disconnecting: #not self.state:
            # check for all incoming data (no timeout, non-blocking)
            self.board.check_activity(self.ser, 1)

            # don't burden the CPU
            time.sleep(0.01)

        quit()

    def send_command(self, char):
        pass

    def start_stream(self):
        self.send_command('b')
        pass

    def stop_stream(self):
        self.send_command('s')
        pass

    def set_channels(self, chan_list):
        """
        chan_list is an array of four elements, each elements corresponds to one channel. If an element is set to 0 then that channel on the board will be turned off. If an element is set to 1 then that element on the board will be turned on.
        """
        on_chars = '!@#$'
        off_chars = '1234'
        out_string = ''
        for indx, chan in enumerate(chan_list):
            print(chan, indx)
            if chan == 1:
                out_string += on_chars[indx]
            elif chan == 0:
                out_string += off_chars[indx]
            else:
                print("Invalid channel list. The format should be: [1, 1, 1, 1] and it should only have 0 or 1")
        print(out_string)
        self.send_command(out_string)

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
        self.disconnecting = True

        print('Bye')

    def print_raw_data(self):
        pass

    def get_board_output(self):
        pass

    def bytes2data(self):
        pass



board = Ganglion(port='COM6')
# board.set_channels([1, 0, 0, 1])
board.connect()
# time.sleep(1)
board.disconnect()
