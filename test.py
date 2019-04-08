from ganglion_ble import Ganglion

# C409C1112D76
board = Ganglion(port='COM6', mac_addrs='EF35B41DB3DF')
board.connect()
print('connected')
board.send_board_command('[')
# board.set_channels([1, 0, 0, 1])
board.send_board_command('b')
board.start_stream()
# time.sleep(1)
# board.disconnect()
