;;; This patch handles the specifics for area rando doors;;; 
;;; - refill at Tourian elevator
;;; - croc area exit bugfix
;;;
;;; compile with asar v1.81 (https://github.com/RPGHacker/asar/releases/tag/v1.81)

lorom
arch 65816

incsrc "macros.asm"

;;; For refill
!samus_health          = $09c2
!samus_max_health      = $09c4
!samus_reserve         = $09d6
!samus_max_reserve     = $09d4
!samus_missiles        = $09c6
!samus_max_missiles    = $09c8
!samus_supers          = $09ca
!samus_max_supers      = $09cc
!samus_pbs             = $09ce
!samus_max_pbs         = $09d0
!samus_reserve         = $09d6
!samus_max_reserve     = $09d4

org $8ff700
        db $ca                  ; marker to detect we have area rando

;;; "ship refill" for tourian elevator
%export(full_refill)
	lda !samus_max_health
	sta !samus_health
	lda !samus_max_reserve
	sta !samus_reserve
	lda !samus_max_missiles
	sta !samus_missiles
	lda !samus_max_supers
	sta !samus_supers
	lda !samus_max_pbs
	sta !samus_pbs
.end:
	rts

;;; door pointers for room below botwoon
below_botwoon_doors:
	dw $a7d4,$a534

warnpc $8ff72f

;;; add door in room below botwoon etank (room header update)
org $8FD706
    dw below_botwoon_doors

;;; Tourian Elevator door ASM ptr
org $83922c
	dw full_refill
