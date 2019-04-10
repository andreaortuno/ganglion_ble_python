import bglib
import serial
from serial import Serial
import time
import struct
import datetime
import sys
from pynput import keyboard
import struct
import numpy as np
import csv
from bitstring import BitArray
from pylsl import StreamInfo, StreamOutlet

# service for communication, as per docs
BLE_SERVICE = [0xfe, 0x84]

# characteristics of interest
BLE_CHAR_RECEIVE = "2d30c082f39f4ce6923f3484ea480596"
BLE_CHAR_SEND = "2d30c083f39f4ce6923f3484ea480596"
BLE_CHAR_DISCONNECT = "2d30c084f39f4ce6923f3484ea480596"

STATE_STANDBY = 0
STATE_CONNECTING = 1
STATE_FINDING_SERVICES = 2
STATE_FINDING_ATTRIBUTES = 3
STATE_LISTENING_MEASUREMENTS = 4
STATE_STREAMING = 5



class Ganglion():
    def __init__(self, port=None, baud_rate=230400, mac_addrs=None):

        if not port or not mac_addrs:
            raise ValueError('You need to have a port name adn a mac_addrs')
        self.port = port
        self.baud_rate = baud_rate
        self.board = bglib.BGLib()
        self.board.packet_mode = False
        self.mac = mac_addrs
        self.board.debug = False

        self.state = STATE_STANDBY
        self.connected = False
        self.disconnecting = False
        self.received_found = False
        self.zero_packet = False
        self.last_values = np.array([0, 0, 0, 0])
        self.last_id = 9999
        self.dropped_packets = 0

        #for lsl streaming
        self.info = StreamInfo('OpenBCIGanglion', 'EEG', 4, 200, 'float32', 'mac_addrs')

        self.outlet = StreamOutlet(self.info)

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
            if not self.connected and ''.join(['%02X' % b for b in args["sender"][::-1]]) == self.mac:
                self.board.send_command(self.ser, self.board.ble_cmd_gap_connect_direct(args['sender'], args['address_type'], 0x20, 0x30, 0x100, 0))
                self.board.check_activity(self.ser, 1)
                self.state = STATE_CONNECTING

        # connection_status handler
        def my_ble_evt_connection_status(sender, args):

            if (args['flags'] & 0x05) == 0x05:
                # connected, now perform characteristic discovery
                print "Connected to %s" % ':'.join(['%02X' % b for b in args['address'][::-1]])
                self.connection_handle = args['connection']

                self.board.send_command(self.ser, self.board.ble_cmd_attclient_find_information(args['connection'], 0x0001, 0xFFFF))
                self.board.check_activity(self.ser, 1)
                self.state = STATE_FINDING_ATTRIBUTES

        # attclient_group_found handler
        def ble_evt_attclient_find_information_found(sender, args):

            # check for OpenBCI characteristics
            if BLE_CHAR_RECEIVE.upper() == ''.join(['%02X' % b for b in args['uuid'][::-1]]):
                print('Receive characteristic found!')
                self.received_found = True
                self.receive_handle = args['chrhandle']

            elif '2902' == ''.join(['%02X' % b for b in args['uuid'][::-1]]) and self.received_found:
                print('Receive ccc characteristic found!')
                self.receive_handle_ccc = args['chrhandle']
                self.received_found = False

            elif BLE_CHAR_SEND.upper() == ''.join(['%02X' % b for b in args['uuid'][::-1]]):
                print('Send characteristic found!')
                self.send_handle = args['chrhandle']

            elif BLE_CHAR_DISCONNECT.upper() == ''.join(['%02X' % b for b in args['uuid'][::-1]]):
                print('Disconnect characteristic found!')
                self.disconnect_handle = args['chrhandle']
                self.connected = True

        # attclient_attribute_value handler
        def my_ble_evt_attclient_attribute_value(sender, args):
            if self.connected:
                # check for a new value from the connected peripheral's ganglion measurement attribute
                if args['connection'] == self.connection_handle and args['atthandle'] == self.receive_handle:
                    # add function to do
                    if self.stream_type.upper() == 'TXT':
                        self.save_to_file(self.bytes2data(args['value']))
                    if self.stream_type.upper() == 'LSL':
                        if len(args['value']) == 4:
                            self.outlet.push_sample(args['value'])
                        elif len(args['value']) == 8:
                            self.outlet.push_sample(args['value'][:4])
                            self.outlet.push_sample(args['value'][4:])


        # add handlers for BGAPI events
        self.board.ble_evt_gap_scan_response += my_ble_evt_gap_scan_response
        self.board.ble_evt_connection_status += my_ble_evt_connection_status
        self.board.ble_evt_attclient_find_information_found += ble_evt_attclient_find_information_found
        self.board.ble_evt_attclient_attribute_value += my_ble_evt_attclient_attribute_value


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
        self.connected = False
        self.board.send_command(self.ser, self.board.ble_cmd_gap_discover(2))
        self.board.check_activity(self.ser, 1)


        while not self.connected: #not self.state:
            # check for all incoming data (no timeout, non-blocking)
            self.board.check_activity(self.ser, 1)

            # don't burden the CPU
            time.sleep(0.01)

    def send_board_command(self, string):
        # check if we just finished searching for attributes
        self.board.check_activity(self.ser, 1)
        if self.connected == True:
            for char in string:
                self.board.check_activity(self.ser, 1)
                if self.send_handle > 0:
                    print "Writting to send characteristic"
                    self.state = STATE_LISTENING_MEASUREMENTS
                    self.board.send_command(self.ser, self.board.ble_cmd_attclient_attribute_write(self.connection_handle, self.send_handle, [ord(char), 0x00]))
                    self.board.check_activity(self.ser, 1)
                else:
                    print "Could not find send attribute"


    def start_stream(self, type="txt"):
        # found the measurement + client characteristic configuration, so enable notifications
        # (this is done by writing 0x01 to the client characteristic configuration attribute)
        print('Starting stream')
        self.board.check_activity(self.ser, 1)
        self.board.send_command(self.ser, self.board.ble_cmd_attclient_attribute_write(self.connection_handle, self.receive_handle_ccc, [0x01, 0x00]))
        self.stream_type = type
        while True:
            # self.board.send_command(self.ser, self.board.ble_cmd_attclient_attribute_write(self.connection_handle, self.receive_handle_ccc, [0x01, 0x00]))
            self.board.check_activity(self.ser, .01)

    def set_channels(self, chan_list):
        """
        chan_list is an array of four elements, each elements corresponds to one channel. If an element is set to 0 then that channel on the board will be turned off. If an element is set to 1 then that element on the board will be turned on.
        """
        on_chars = '!@#$'
        off_chars = '1234'
        out_string = ''
        for indx, chan in enumerate(chan_list):
            if chan == 1:
                out_string += on_chars[indx]
            elif chan == 0:
                out_string += off_chars[indx]
            else:
                print("Invalid channel list. The format should be: [1, 1, 1, 1] and it should only have 0 or 1")
        self.send_board_command(out_string)

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

    def bytes2data(self, raw_data):
        start_byte = raw_data[0]
        bit_array = BitArray()
        self.check_dropped(start_byte)
        if start_byte == 0:
            # we can just append everything to the bitarray
            print("Zero Packet")
            for byte in raw_data[1:13]:
                bit_array.append('0b{0:08b}'.format(byte))
            results = []
            # and split it into 24-bit chunks here
            for sub_array in bit_array.cut(24):
                # calling ".int" interprets the value as signed 2's complement
                results.append(sub_array.int)
            self.last_values = np.array(results)
            # print(self.last_values)
            return [np.append(start_byte, self.last_values)]
        elif start_byte >=1 and start_byte <=100:
            for byte in raw_data[1:-1]:
                bit_array.append('0b{0:08b}'.format(byte))
            deltas = []
            for sub_array in bit_array.cut(18):
                deltas.append(self.decompress_signed(sub_array))

            delta1 , delta2 = np.array(deltas[:4]) , np.array(deltas[4:])
            self.last_values1 = self.last_values - delta1
            # print(self.last_values1)
            self.last_values = self.last_values1 - delta2
            # print(self.last_values)
            return [self.last_values1, self.last_values]

        elif start_byte >=101 and start_byte <=200:
            for byte in raw_data[1:]:
                bit_array.append('0b{0:08b}'.format(byte))
            deltas = []
            for sub_array in bit_array.cut(19):
                deltas.append(self.decompress_signed(sub_array))

            delta1 , delta2 = np.array(deltas[:4]) , np.array(deltas[4:])
            self.last_values1 = self.last_values - delta1
            # print(self.last_values1)
            self.last_values = self.last_values1 - delta2
            # print(self.last_values)
            return [np.append(start_byte,self.last_values1), np.append(start_byte,self.last_values)]


    # process a bitarray where the sign bit is the LSB
    # return the signed integer result
    def decompress_signed(self, bit_array):
        result = bit_array.int
        if bit_array.endswith('0b1'):   # negative value
            result -= 1 # flip all the bits and add a zero at the end
        return result


    def check_dropped(self, packet_id):
        #check for dropped packets
        if self.last_id != 9999:
            if packet_id != 0 and packet_id > self.last_id:
                if int(packet_id) - 1 not in [self.last_id, 100]:
                    # print("Warning: dropped " + str(- self.last_id + packet_id) + " packets.")
                    print([self.last_id, packet_id, packet_id - self.last_id])
                    if self.last_id:
                        self.dropped_packets += (- self.last_id + packet_id)
                    else:
                        self.dropped_packets += (- self.last_id + packet_id-100)

                    if packet_id in [100, 200]:
                        print('Dropped in this cycle: ' + str(self.dropped_packets))
                        self.dropped_packets = 0

            elif self.last_id not in [100, 200] and packet_id != 0:
                # print("Warning: dropped " + str(99 - self.last_id + packet_id) + " packets.")
                print([self.last_id, packet_id, 200-self.last_id + packet_id -100])
                print('Dropped in this cycle: ' + str(self.dropped_packets + 200 - self.last_id))
                self.dropped_packets = 0
            else:
                if self.last_id not in [200, 100]:
                    self.dropped_packets += 1
                    print('Dropped in this cycle: ' + str(self.dropped_packets + 200 - self.last_id))
                    self.dropped_packets = 0


        self.last_id = packet_id

    def save_to_file(self, data, filename='Ganglion_Data.txt'):

        Data = data

        with open(filename, 'ab') as file:
            writer = csv.writer(file)
            writer.writerows(Data)

        file.close()


    def graph_data(self, data):
        pass
