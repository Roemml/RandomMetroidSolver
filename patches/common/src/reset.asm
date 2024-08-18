;;; reset to last save on Start+Select+L+R
;;; Based on patch by total: https://metroidconstruction.com/resource.php?id=421
;;; (removed the "quick reload" part)
;;; 
;;; compile with asar v1.81 (https://github.com/RPGHacker/asar/releases/tag/v1.81)

arch 65816
lorom

incsrc "sym/base.asm"
incsrc "sym/stats.asm"

incsrc "constants.asm"
incsrc "macros.asm"

!button_combo = $3030   ; L + R + Select + Start

;; Hook state $08 (main gameplay) for quick reset combo check
org $828BB3
	jsl hook_main

org $90e8a1
        jsl give_control_back_to_samus : nop : nop

org $81fa80
hook_main:
	jsl $A09169  ; run hi-jacked instruction
check_reload:
	php
	%ai16()
	pha
	;; Disable quick reload during the Samus fanfare
	lda $0A44
	cmp #$E86A
	beq .end
	lda $8B      ; Controller 1 input
	and #!button_combo
	cmp #!button_combo
	bne .end ; If any of the 4 inputs are not currently held, then do not reset.
	lda $8F      ; Newly pressed controller 1 input
	and #!button_combo
	beq .end   ; Reset only if at least one of the 4 inputs is newly pressed
	;; actually reset :
	;; increment reset count
	lda !stat_resets : jsl base_inc_stat
        ;; if you hold the combo on SNES classic, actual reset occurs,
        ;; use this as special value to not count reset twice in the stats in that case
        lda #!dec_reset_flag : sta !softreset
	;; update region time
	jsl stats_update_and_store_region_time
	;; save stats
	jsl base_save_last_stats
	;; reload last save
	jsl $82be17       ; Stop sounds
	lda !current_save_slot : jsl base_LoadGame
	jsl $80858C	  ; load map
	lda #$0006 : sta !game_state         ; Goto game mode 6 (load game)
.end:
	pla
	plp
	rtl

give_control_back_to_samus:
        ;; hijacked code
        LDA #$E725
        STA $0A44
        ;; set soft reset flag back to normal (samus fanfare is long enough that SNES classic reset didn't trigger here)
        lda #!reset_flag : sta !softreset
        rtl

warnpc $81fadf
