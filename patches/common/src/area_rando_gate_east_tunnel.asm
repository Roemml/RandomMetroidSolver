;;; compile with asar

arch snes.cpu
lorom

;;; remove gate at east tunnel by ending the plm list
org $8fc41d
        dw $0000
