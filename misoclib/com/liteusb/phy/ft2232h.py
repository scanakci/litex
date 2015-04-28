from migen.fhdl.std import *
from migen.flow.actor import *
from migen.actorlib.fifo import SyncFIFO, AsyncFIFO
from migen.fhdl.specials import *
from migen.genlib.cdc import MultiReg

from misoclib.com.liteusb.common import *


def anti_starvation(module, timeout):
        en = Signal()
        max_time = Signal()
        if timeout:
            t = timeout - 1
            time = Signal(max=t+1)
            module.comb += max_time.eq(time == 0)
            module.sync += If(~en,
                    time.eq(t)
                ).Elif(~max_time,
                    time.eq(time - 1)
                )
        else:
            module.comb += max_time.eq(0)
        return en, max_time


class FT2232HPHYSynchronous(Module):
    def __init__(self, pads,
                 fifo_depth=32,
                 read_time=16,
                 write_time=16):
        dw = flen(pads.data)

        #
        # Read / Write Fifos
        #

        # Read Fifo (Ftdi --> SoC)
        read_fifo = RenameClockDomains(AsyncFIFO(phy_description(8), fifo_depth),
            {"write": "ftdi", "read": "sys"})
        read_buffer = RenameClockDomains(SyncFIFO(phy_description(8), 4),
            {"sys": "ftdi"})
        self.comb += read_buffer.source.connect(read_fifo.sink)

        # Write Fifo (SoC --> Ftdi)
        write_fifo = RenameClockDomains(AsyncFIFO(phy_description(8), fifo_depth),
            {"write": "sys", "read": "ftdi"})

        self.submodules += read_fifo, read_buffer, write_fifo

        #
        # Sink / Source interfaces
        #
        self.sink = write_fifo.sink
        self.source = read_fifo.source

        #
        # Read / Write Arbitration
        #
        wants_write = Signal()
        wants_read = Signal()

        txe_n = Signal()
        rxf_n = Signal()

        self.comb += [
            txe_n.eq(pads.txe_n),
            rxf_n.eq(pads.rxf_n),
            wants_write.eq(~txe_n & write_fifo.source.stb),
            wants_read.eq(~rxf_n & read_fifo.sink.ack),
        ]

        read_time_en, max_read_time = anti_starvation(self, read_time)
        write_time_en, max_write_time = anti_starvation(self, write_time)

        data_w_accepted = Signal(reset=1)

        fsm = FSM(reset_state="READ")
        self.submodules += RenameClockDomains(fsm, {"sys": "ftdi"})

        fsm.act("READ",
            read_time_en.eq(1),
            If(wants_write,
                If(~wants_read | max_read_time,
                    NextState("RTW")
                )
            )
        )
        fsm.act("RTW",
            NextState("WRITE")
        )
        fsm.act("WRITE",
            write_time_en.eq(1),
            If(wants_read,
                If(~wants_write | max_write_time,
                    NextState("WTR")
                )
            ),
            write_fifo.source.ack.eq(wants_write & data_w_accepted)
        )
        fsm.act("WTR",
            NextState("READ")
        )

        #
        # Read / Write Actions
        #

        data_w = Signal(dw)
        data_r = Signal(dw)
        data_oe = Signal()

        pads.oe_n.reset = 1
        pads.rd_n.reset = 1
        pads.wr_n.reset = 1

        self.sync.ftdi += [
            If(fsm.ongoing("READ"),
                data_oe.eq(0),

                pads.oe_n.eq(0),
                pads.rd_n.eq(~wants_read),
                pads.wr_n.eq(1)

            ).Elif(fsm.ongoing("WRITE"),
                data_oe.eq(1),

                pads.oe_n.eq(1),
                pads.rd_n.eq(1),
                pads.wr_n.eq(~wants_write),

                data_w_accepted.eq(~txe_n)

            ).Else(
                data_oe.eq(1),

                pads.oe_n.eq(~fsm.ongoing("WTR")),
                pads.rd_n.eq(1),
                pads.wr_n.eq(1)
            ),
                read_buffer.sink.stb.eq(~pads.rd_n & ~rxf_n),
                read_buffer.sink.data.eq(data_r),
                If(~txe_n & data_w_accepted,
                    data_w.eq(write_fifo.source.data)
                )
        ]

        #
        # Databus Tristate
        #
        self.specials += Tristate(pads.data, data_w, data_oe, data_r)

        self.debug = Signal(8)
        self.comb += self.debug.eq(data_r)


class FT2232HPHYAsynchronous(Module):
    def __init__(self, pads, clk_freq,
                 fifo_depth=32,
                 read_time=16,
                 write_time=16):
        dw = flen(pads.data)

        #
        # Read / Write Fifos
        #

        # Read Fifo (Ftdi --> SoC)
        read_fifo = SyncFIFO(phy_description(8), fifo_depth)

        # Write Fifo (SoC --> Ftdi)
        write_fifo = SyncFIFO(phy_description(8), fifo_depth)

        self.submodules += read_fifo, write_fifo

        #
        # Sink / Source interfaces
        #
        self.sink = write_fifo.sink
        self.source = read_fifo.source

        #
        # Read / Write Arbitration
        #
        wants_write = Signal()
        wants_read = Signal()

        txe_n = Signal()
        rxf_n = Signal()

        self.specials += [
            MultiReg(pads.txe_n, txe_n),
            MultiReg(pads.rxf_n, rxf_n)
        ]

        self.comb += [
            wants_write.eq(~txe_n & write_fifo.source.stb),
            wants_read.eq(~rxf_n & read_fifo.sink.ack),
        ]

        read_time_en, max_read_time = anti_starvation(self, read_time)
        write_time_en, max_write_time = anti_starvation(self, write_time)

        fsm = FSM(reset_state="READ")
        self.submodules += fsm

        read_done = Signal()
        write_done = Signal()
        commuting = Signal()

        fsm.act("READ",
            read_time_en.eq(1),
            If(wants_write & read_done,
                If(~wants_read | max_read_time,
                    commuting.eq(1),
                    NextState("RTW")
                )
            )
        )
        fsm.act("RTW",
            NextState("WRITE")
        )
        fsm.act("WRITE",
            write_time_en.eq(1),
            If(wants_read & write_done,
                If(~wants_write | max_write_time,
                    commuting.eq(1),
                    NextState("WTR")
                )
            )
        )
        fsm.act("WTR",
            NextState("READ")
        )

        #
        # Databus Tristate
        #
        data_w = Signal(dw)
        data_r_async = Signal(dw)
        data_r = Signal(dw)
        data_oe = Signal()
        self.specials += [
            Tristate(pads.data, data_w, data_oe, data_r_async),
            MultiReg(data_r_async, data_r)
        ]

        #
        # Read / Write Actions
        #
        pads.wr_n.reset = 1
        pads.rd_n.reset = 1

        read_fsm = FSM(reset_state="IDLE")
        read_counter = Counter(8)
        self.submodules += read_fsm, read_counter

        read_fsm.act("IDLE",
            read_done.eq(1),
            read_counter.reset.eq(1),
            If(fsm.ongoing("READ") & wants_read,
                If(~commuting,
                    NextState("PULSE_RD_N")
                )
            )
        )
        read_fsm.act("PULSE_RD_N",
            pads.rd_n.eq(0),
            read_counter.ce.eq(1),
            If(read_counter.value == 15, # XXX Compute exact value
                NextState("ACQUIRE_DATA")
            )
        )
        read_fsm.act("ACQUIRE_DATA",
            read_fifo.sink.stb.eq(1),
            read_fifo.sink.data.eq(data_r),
            NextState("WAIT_RXF_N")
        )
        read_fsm.act("WAIT_RXF_N",
            If(rxf_n,
                NextState("IDLE")
            )
        )

        write_fsm = FSM(reset_state="IDLE")
        write_counter = Counter(8)
        self.submodules += write_fsm, write_counter

        write_fsm.act("IDLE",
            write_done.eq(1),
            write_counter.reset.eq(1),
            If(fsm.ongoing("WRITE") & wants_write,
                If(~commuting,
                    NextState("SET_DATA")
                )
            )
        )
        write_fsm.act("SET_DATA",
            data_oe.eq(1),
            data_w.eq(write_fifo.source.data),
            write_counter.ce.eq(1),
            If(write_counter.value == 5, # XXX Compute exact value
                write_counter.reset.eq(1),
                NextState("PULSE_WR_N")
            )
        )
        write_fsm.act("PULSE_WR_N",
            data_oe.eq(1),
            data_w.eq(write_fifo.source.data),
            pads.wr_n.eq(0),
            write_counter.ce.eq(1),
            If(write_counter.value == 15, # XXX Compute exact value
                NextState("WAIT_TXE_N")
            )
        )
        write_fsm.act("WAIT_TXE_N",
            If(txe_n,
                write_fifo.source.ack.eq(1),
                NextState("IDLE")
            )
        )
