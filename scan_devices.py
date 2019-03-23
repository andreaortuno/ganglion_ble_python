#!/usr/bin/env python

""" Bluegiga BGAPI/BGLib implementation
Changelog:
    2013-04-11 - Initial release
============================================
Bluegiga BGLib Python interface library test scanner app
2013-04-10 by Jeff Rowberg <jeff@rowberg.net>
Updates should (hopefully) always be available at https://github.com/jrowberg/bglib
============================================
BGLib Python interface library code is placed under the MIT license
Copyright (c) 2013 Jeff Rowberg
Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:
The above copyright notice and this permission notice shall be included in
all copies or substantial portions of the Software.
THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
THE SOFTWARE.
===============================================
"""

__author__ = "Jeff Rowberg"
__license__ = "MIT"
__version__ = "2013-04-11"
__email__ = "jeff@rowberg.net"


import bglib, serial, time, datetime, struct

# handler to notify of an API parser timeout condition
def my_timeout(sender, args):
    global ble, ser
    # might want to try the following lines to reset, though it probably
    # wouldn't work at this point if it's already timed out:
    print('here')
    ble.send_command(ser, ble.ble_cmd_system_reset(0))
    ble.check_activity(ser, 1)
    print "BGAPI parser timed out. Make sure the BLE device is in a known/idle state."

# handler to print scan responses with a timestamp
def my_ble_evt_gap_scan_response(sender, args):
    print "gap_scan_response",
    t = datetime.datetime.now()
    disp_list = []
    disp_list.append("%ld.%03ld" % (time.mktime(t.timetuple()), t.microsecond/1000))
    disp_list.append("%d" % args["rssi"])
    disp_list.append("%d" % args["packet_type"])
    disp_list.append("%s" % ''.join(['%02X' % b for b in args["sender"][::-1]]))
    disp_list.append("%d" % args["address_type"])
    disp_list.append("%d" % args["bond"])
    disp_list.append("%s" % ''.join(['%02X' % b for b in args["data"]]))
    print ' '.join(disp_list)

def main():
    global ble, ser
    # NOTE: CHANGE THESE TO FIT YOUR TEST SYSTEM
    port_name = "COM6"
    baud_rate = 0
    packet_mode = False

    # create BGLib object
    ble = bglib.BGLib()
    ble.packet_mode = packet_mode

    # # add handler for BGAPI timeout condition (hopefully won't happen)
    ble.on_timeout += my_timeout

    # add handler for the gap_scan_response event
    ble.ble_evt_gap_scan_response += my_ble_evt_gap_scan_response

    # create serial port object and flush buffers
    ser = serial.Serial(port=port_name, baudrate=baud_rate, timeout=1)
    ser.flushInput()
    ser.flushOutput()

    # disconnect if we are connected already
    ble.send_command(ser, ble.ble_cmd_connection_disconnect(0))
    response = ser.read(7)

    # stop advertising if we are advertising already
    ble.send_command(ser, ble.ble_cmd_gap_set_mode(0, 0))
    response = ser.read(6)

    # stop scanning if we are scanning already
    ble.send_command(ser, ble.ble_cmd_gap_end_procedure())
    response = ser.read(6)

    # set scan parameters
    ble.send_command(ser, ble.ble_cmd_gap_set_scan_parameters(0xC8, 0xC8, 1))
    response = ser.read(6)

    # start scanning now
    ble.send_command(ser, ble.ble_cmd_gap_discover(1))
    response = ser.read(6)

    while (1):
        # check for all incoming data (no timeout, non-blocking)
        while (ser.inWaiting()): bgapi_parse(ord(ser.read()));
        # don't burden the CPU
        time.sleep(0.01)

global bgapi_rx_buffer, bgapi_rx_expected_length
bgapi_rx_buffer = []
bgapi_rx_expected_length = 0

def bgapi_parse(b):
    global bgapi_rx_buffer, bgapi_rx_expected_length
    if len(bgapi_rx_buffer) == 0 and (b == 0x00 or b == 0x80):
        bgapi_rx_buffer.append(b)
    elif len(bgapi_rx_buffer) == 1:
        bgapi_rx_buffer.append(b)
        bgapi_rx_expected_length = 4 + (bgapi_rx_buffer[0] & 0x07) + bgapi_rx_buffer[1]
    elif len(bgapi_rx_buffer) > 1:
        bgapi_rx_buffer.append(b)

    #print '%02X: %d, %d' % (b, len(bgapi_rx_buffer), bgapi_rx_expected_length)
    if bgapi_rx_expected_length > 0 and len(bgapi_rx_buffer) == bgapi_rx_expected_length:
        #print '<=[ ' + ' '.join(['%02X' % b for b in bgapi_rx_buffer ]) + ' ]'
        packet_type, payload_length, packet_class, packet_command = bgapi_rx_buffer[:4]
        bgapi_rx_payload = b''.join(chr(i) for i in bgapi_rx_buffer[4:])
        if packet_type & 0x80 == 0x00: # response
            bgapi_filler = 0
        else: # event
            if packet_class == 0x06: # gap
                if packet_command == 0x00: # scan_response
                    rssi, packet_type, sender, address_type, bond, data_len = struct.unpack('<bB6sBBB', bgapi_rx_payload[:11])
                    sender = [ord(b) for b in sender]
                    data_data = [ord(b) for b in bgapi_rx_payload[11:]]
                    display = 1

                    # parse all ad fields from ad packet
                    ad_fields = []
                    this_field = []
                    ad_flags = 0
                    ad_services = []
                    ad_local_name = []
                    ad_tx_power_level = 0
                    ad_manufacturer = []

                    bytes_left = 0
                    for b in data_data:
                        if bytes_left == 0:
                            bytes_left = b
                            this_field = []
                        else:
                            this_field.append(b)
                            bytes_left = bytes_left - 1
                            if bytes_left == 0:
                                ad_fields.append(this_field)
                                if this_field[0] == 0x01: # flags
                                    ad_flags = this_field[1]
                                if this_field[0] == 0x02 or this_field[0] == 0x03: # partial or complete list of 16-bit UUIDs
                                    for i in xrange((len(this_field) - 1) / 2):
                                        ad_services.append(this_field[-1 - i*2 : -3 - i*2 : -1])
                                if this_field[0] == 0x04 or this_field[0] == 0x05: # partial or complete list of 32-bit UUIDs
                                    for i in xrange((len(this_field) - 1) / 4):
                                        ad_services.append(this_field[-1 - i*4 : -5 - i*4 : -1])
                                if this_field[0] == 0x06 or this_field[0] == 0x07: # partial or complete list of 128-bit UUIDs
                                    for i in xrange((len(this_field) - 1) / 16):
                                        ad_services.append(this_field[-1 - i*16 : -17 - i*16 : -1])
                                if this_field[0] == 0x08 or this_field[0] == 0x09: # shortened or complete local name
                                    ad_local_name = this_field[1:]
                                if this_field[0] == 0x0A: # TX power level
                                    ad_tx_power_level = this_field[1]

                                # OTHER AD PACKET TYPES NOT HANDLED YET

                                if this_field[0] == 0xFF: # manufactuerer specific data
                                    ad_manufacturer.append(this_field[1:])

                    if display:
                        #print "gap_scan_response: rssi: %d, packet_type: %d, sender: %s, address_type: %d, bond: %d, data_len: %d" % \
                        #    (rssi, packet_type, ':'.join(['%02X' % ord(b) for b in sender[::-1]]), address_type, bond, data_len)

                        t = datetime.datetime.now()

                        disp_list = []
                        for c in "trpsabd":
                            if c == 't':
                                disp_list.append("%ld.%03ld" % (time.mktime(t.timetuple()), t.microsecond/1000))
                            elif c == 'r':
                                disp_list.append("%d" % rssi)
                            elif c == 'p':
                                disp_list.append("%d" % packet_type)
                            elif c == 's':
                                disp_list.append("%s" % ''.join(['%02X' % b for b in sender[::-1]]))
                            elif c == 'a':
                                disp_list.append("%d" % address_type)
                            elif c == 'b':
                                disp_list.append("%d" % bond)
                            elif c == 'd':
                                disp_list.append("%s" % ''.join(['%02X' % b for b in data_data]))

                        print ' '.join(disp_list)

        bgapi_rx_buffer = []

if __name__ == '__main__':
    main()
