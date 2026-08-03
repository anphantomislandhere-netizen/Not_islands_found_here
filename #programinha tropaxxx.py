import math
#Isso daqui é simples:
#Encontrou ilha, bote as coordenadas dele no x e z, e então escolha a quantidade de ramos, nesse caso TxT de tamanho, aí você copia e cola que provavelmente encontra outra ilha
#Obs: isso não serve para encontrar ilhas, isso pressupôe que você já achou ao menos uma
#Evite usar apenas 3064, pois ela pode gerar um erro enorme para coordenadas distantes, por exemplo: 41 chunks☠️☠️
#Por isso recomendo você deixar aqueles programas no mínimo em 30 chunks e se puder em 50, eles não demoram e ainda entregam uma boa região
#import aura as tropaxxx
#Programinha tropaxxx
x, z = -23520, 19600

#TDAH né, quero evitar problemas de trocar a seed na hora de testar
SEED = 46

#Aí tu pode meter qualquer raio dessa poha, raio de "sla" ramos
sla = 4

#Raio real, ss tropaxxx, eu descobri:
T = (3064 + 28408 / 171103) * 16

print(f"Aqui tem {sla}x{sla} com {sla * sla} ramos")

for c in range(sla): 
    for l in range(sla):
        a = x + T * c
        b = z + T * l

        areal = math.floor(a)
        breal = math.floor(b)
        print(f"/tp {areal} ~ {breal}")
print(f"É a seed {SEED}, não se esqueça")