##############################################################################
#
# Compilation of SoC RTL files
#
##############################################################################

set HDL_WORK "work"

hdl_compile -v 2005 {
	../../misoc/build/simdesign-kc705.v
}