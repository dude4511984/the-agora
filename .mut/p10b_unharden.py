p='kin_diary/agora/node.py'; s=open(p).read()
s=s.replace("        if key.lower() in self.evicted:", "        if key in self.evicted:  # UN-HARDEN", 1)
open(p,'w').write(s); print("[un-harden applied]")
