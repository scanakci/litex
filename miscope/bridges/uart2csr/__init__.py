from migen.fhdl.structure import *
from migen.genlib.misc import *
from migen.genlib.cdc import *
from migen.bus import csr

from miscope.bridges.uart2csr.uart import *

WRITE_CMD = 0x01
READ_CMD = 0x02
CLOSE_CMD = 0x03

class Uart2Csr(Module):
	def __init__(self, clk_freq, baud):
		# Uart interface
		self.rx = Signal()
		self.tx = Signal()
		
		# Csr interface
		self.csr = csr.Interface(32, 8)
		
	###
		
		uart = Uart(clk_freq, baud)
		self.specials +=uart

		#
		# In/Out
		#
		self.comb +=[
			uart.rx.eq(self.rx),
			self.tx.eq(uart.tx)
		]

		cmd = Signal(8)
		cnt = Signal(3)
		sr = Signal(32)
		burst_cnt = Signal(8)
		addr = Signal(32)
		data = Signal(8)

		#
		# Global
		#
		self.sync +=[
			If(fsm.ongoing(fsm.IDLE), cnt.eq(0)
			).Elif(uart_rx_ev, cnt.eq(cnt + 1)),

			sr.eq(Cat(uart.rx_dat, sr[0:24]))
		]

		# FSM
		fsm = FSM("IDLE", 
				  "GET_BL", "GET_ADDR", 
				  "GET_DATA", "WRITE_CSR",
				   "READ_CSR", "SEND_DATA")

		# State done
		get_bl_done = Signal()
		get_addr_done = Signal()
		get_data_done = Signal()
		send_data_done = Signal()

		#
		# Idle
		#
		fsm.act(fsm.IDLE,
			If(uart.rx_ev and (uart.rx_dat == WRITE_CMD or uart.rx_dat == READ_CMD),
				fsm.next_state(fsm.GET_BL)
			)
		)

		self.sync +=[
			If(fsm.ongoing(fsm.IDLE) and uart_rx_env,
				cmd.eq(uart.rx_dat)
			)	

		]

		#
	    # Get burst length
	    #
		fsm.act(fsm.GET_BL,
			If(get_bl_done,
				fsm.next_state(fsm.GET_ADDR)
			)
		)

		self.comb += get_bl_done.eq(uart_rx_ev and fsm.ongoing(fsm.GET_BL))

		self.sync +=[
			If(get_bl_done,
				burst_cnt.eq(uart.rx_dat)
			)
		]

		#
		# Get address
		#
		fsm.act(fsm.GET_ADDR,
			If(get_addr_done and cmd == WRITE_CMD,
				fsm.next_state(fsm.GET_DATA)
			).Elif(get_addr_done and cmd == READ_CMD,
				fsm.next_state(fsm.READ_CSR)
			)	
		)

		self.comb += get_addr_done.eq(uart_rx_ev and rx_cnt == 4 and fsm.ongoing(fsm.GET_ADDR))
		
		self.sync +=[
			If(get_addr_done,
				addr.eq(sr)
			).Elif(write_data_done or send_data_done,
				addr.eq(addr + 4)
			)
		]

		#
		# Get data
		#
		fsm.act(fsm.GET_DATA,
			If(get_data_done,
				fsm.next_state(fsm.IDLE)
			)
		)

		self.comb += get_data_done.eq(uart_rx_ev and fsm.ongoing(fsm.GET_DATA))	

		self.sync +=[
			If(get_data_done,
				burst_cnt.eq(burst_cnt-1),
				data.eq(uart.rx_dat)
			)
		]

		#
		# Write Csr
		#
		fsm.act(fsm.WRITE_CSR,
			If((not burst_cnt), 
				fsm.next_state(fsm.IDLE)
			).Else(fsm.next_state(fsm.GET_DATA))
		)

		#
		# Read Csr
		#
		fsm.act(fsm.READ_CSR,
			fsm.next_state(fsm.SEND_DATA)
		)

		#
		# Send Data
		#
		fsm.act(fsm.SEND_DATA,
			If(send_data_done and (not burst_cnt),
				fsm.next_state(fsm.IDLE)
			).Elif(send_data_done,
				fsm.next_state(fsm.READ_CSR)
			)
		)

		self.comb += [
				uart.tx_dat.eq(csr.dat_r),
				uart.we.eq(fsm.entering(fsm.SEND_DATA)),
				send_data_done.eq(~uart.we or uart.tx_ev)
		]

		#
		# Csr access
		#
		self.sync +=[
			self.csr.adr.eq(addr),
			self.csr.dat_w.eq(data),
			If(fsm.ongoing(fsm.WRITE_CSR,
				self.csr.we.eq(1)
			).Else(
				self.csr.we.eq(0)	
			)
		]


