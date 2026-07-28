"""
Detector de PROBABILIDADE, não de confirmação — o "chunkbase" do bug.

Faz só a parte barata: soma as oitavas de noiseGen1/noiseGen2 (reflection
direta, sem nunca tocar em initializeNoiseField nem em func_180520_a) e
rankeia chunks por o quão favorecidos são. NUNCA gera um chunk de verdade,
nunca confirma bloco nenhum — essa parte (a difícil, cara) fica pro seu
chunkbase / conferência manual / o programa de referência do gringo.

Por não precisar de initializeNoiseField nem func_180520_a, esse programa
nem importa ChunkPrimer nem Blocks — só o necessário pra chegar em
noiseGen1/noiseGen2.
"""

import os
import glob
import csv
import math
import jpype
import jpype.imports
from jpype.types import JInt, JDouble

# =====================================================================
# 1. CONFIGURAÇÃO
# =====================================================================
CAMINHO_BIN = r"C:\Users\Pichau\Desktop\wqgrf\bin\minecraft"
PASTA_PROJETO = r"C:\Users\Pichau\Desktop\wqgrf"
SEED = 12

jars_encontrados = glob.glob(os.path.join(PASTA_PROJETO, "**", "*.jar"), recursive=True)
classpath_completo = [CAMINHO_BIN] + jars_encontrados

if not jpype.isJVMStarted():
    jpype.startJVM("-Djava.awt.headless=true", classpath=classpath_completo)

from net.minecraft.init import Bootstrap
Bootstrap.register()
from net.minecraft.world.gen import ChunkProviderEnd

provider = ChunkProviderEnd(None, jpype.JLong(SEED))

# =====================================================================
# 2. REFLECTION — só até noiseGen1/noiseGen2, nada além disso
# =====================================================================
def _obter_colecao_oitavas(nome_campo: str):
    campo_gen = provider.getClass().getDeclaredField(nome_campo)
    campo_gen.setAccessible(True)
    gen_obj = campo_gen.get(provider)
    campo_colecao = gen_obj.getClass().getDeclaredField("generatorCollection")
    campo_colecao.setAccessible(True)
    return campo_colecao.get(gen_obj)


_colecao_noiseGen1 = _obter_colecao_oitavas("noiseGen1")
_colecao_noiseGen2 = _obter_colecao_oitavas("noiseGen2")

LATTICE_X, LATTICE_Y, LATTICE_Z = 3, 33, 3
SCALE_X = SCALE_Z = 684.412 * 2.0
SCALE_Y = 684.412
_WRAP = 1 << 24

OITAVAS_DOMINANTES = {14, 15}
LIMIAR_NECESSIDADE = 100.0  # um pouco abaixo do limiar teórico (108) — pega
                            # também os "quase" como o de 105,61 que você achou


def _wrap_java(base: float) -> float:
    k = int(math.floor(base))
    frac = base - k
    r = abs(k) % _WRAP
    k = -r if k < 0 else r
    return frac + k


def somar_oitavas_com_subtotal(colecao_oitavas, indices_subtotal, x_off, z_off):
    tamanho = LATTICE_X * LATTICE_Y * LATTICE_Z
    total = jpype.JArray(jpype.JDouble)(tamanho)
    subtotal = [0.0] * tamanho
    amp = 1.0
    for oitava in range(16):
        base_x = _wrap_java(x_off * amp * SCALE_X)
        base_z = _wrap_java(z_off * amp * SCALE_Z)
        antes = list(total) if oitava in indices_subtotal else None
        colecao_oitavas[oitava].populateNoiseArray(
            total, JDouble(base_x), JDouble(0.0), JDouble(base_z),
            JInt(LATTICE_X), JInt(LATTICE_Y), JInt(LATTICE_Z),
            JDouble(SCALE_X * amp), JDouble(SCALE_Y * amp), JDouble(SCALE_Z * amp),
            JDouble(amp),
        )
        if antes is not None:
            depois = list(total)
            for i in range(tamanho):
                subtotal[i] += depois[i] - antes[i]
        amp /= 2.0
    return list(total), subtotal


def macro_e_necessidade(cx: int, cz: int, indices_oitavas=OITAVAS_DOMINANTES):
    """(macro, necessidade) — nunca gera bloco, nunca confirma nada.
    Só diz o quão favorecida a coordenada é, pela matemática."""
    x_off, z_off = cx * 2, cz * 2
    total1, sub1 = somar_oitavas_com_subtotal(_colecao_noiseGen1, indices_oitavas, x_off, z_off)
    total2, sub2 = somar_oitavas_com_subtotal(_colecao_noiseGen2, indices_oitavas, x_off, z_off)
    necessidade = max(max(total1), max(total2)) / 512.0
    macro = max(max(sub1), max(sub2)) / 512.0
    return macro, necessidade


# =====================================================================
# 3. VARREDURA (paralela) — só o portão barato
# =====================================================================
def processar_lote(lote, limiar_necessidade=LIMIAR_NECESSIDADE):
    resultados = []
    for cx, cz in lote:
        macro, necessidade = macro_e_necessidade(cx, cz)
        if necessidade >= limiar_necessidade:
            resultados.append((cx, cz, necessidade, macro))
    return resultados


def gerar_lotes(cx_min, cx_max, cz_min, cz_max, tamanho_lote=2000):
    lote = []
    for cx in range(cx_min, cx_max + 1):
        for cz in range(cz_min, cz_max + 1):
            lote.append((cx, cz))
            if len(lote) >= tamanho_lote:
                yield lote
                lote = []
    if lote:
        yield lote


def encontrar_provaveis(cx_min, cx_max, cz_min, cz_max,
                         limiar_necessidade=LIMIAR_NECESSIDADE,
                         n_processos=8, tamanho_lote=2000):
    """Varre a região e devolve só os chunks (cx,cz,necessidade,macro)
    acima do limiar — nada de gerar bloco, nada de confirmar. Isso é
    o que vira insumo pro seu chunkbase."""
    import multiprocessing as mp
    from functools import partial
    from tqdm import tqdm

    total_chunks = (cx_max - cx_min + 1) * (cz_max - cz_min + 1)
    total_lotes = -(-total_chunks // tamanho_lote)
    lotes = gerar_lotes(cx_min, cx_max, cz_min, cz_max, tamanho_lote)
    tarefa = partial(processar_lote, limiar_necessidade=limiar_necessidade)

    candidatos = []
    with mp.Pool(processes=n_processos) as pool:
        barra = tqdm(pool.imap_unordered(tarefa, lotes), total=total_lotes,
                     desc="procurando prováveis", unit="lote")
        for resultado_lote in barra:
            candidatos.extend(resultado_lote)
            barra.set_postfix(candidatos=len(candidatos))

    return candidatos


# =====================================================================
# 4. CLUSTERIZAÇÃO — um "monte" de favorecimento = uma entrada só
# =====================================================================
def agrupar_provaveis(candidatos, raio_adjacencia=1):
    """candidatos: (cx, cz, necessidade, macro). Devolve um representante
    (o de maior necessidade) por monte de chunks vizinhos."""
    dados = {(cx, cz): (necessidade, macro) for cx, cz, necessidade, macro in candidatos}
    visitados = set()
    grupos = []
    offsets = [(dx, dz) for dx in range(-raio_adjacencia, raio_adjacencia + 1)
               for dz in range(-raio_adjacencia, raio_adjacencia + 1) if (dx, dz) != (0, 0)]

    for pos in dados:
        if pos in visitados:
            continue
        pilha = [pos]
        visitados.add(pos)
        grupo = []
        while pilha:
            atual = pilha.pop()
            grupo.append(atual)
            acx, acz = atual
            for dx, dz in offsets:
                viz = (acx + dx, acz + dz)
                if viz in dados and viz not in visitados:
                    visitados.add(viz)
                    pilha.append(viz)
        grupos.append(grupo)

    representantes = []
    for grupo in grupos:
        melhor = max(grupo, key=lambda p: dados[p][0])
        necessidade, macro = dados[melhor]
        representantes.append((melhor[0], melhor[1], necessidade, macro, len(grupo)))

    representantes.sort(key=lambda t: t[2], reverse=True)
    return representantes


# =====================================================================
# 5. SAÍDA — pronta pra usar no chunkbase / conferir manualmente
# =====================================================================
def exportar_csv(representantes, caminho="provaveis_ilhas.csv"):
    with open(caminho, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["chunk_x", "chunk_z", "bloco_x", "bloco_z", "necessidade", "macro", "tamanho_monte", "tp"])
        for cx, cz, necessidade, macro, tamanho in representantes:
            bx, bz = cx * 16, cz * 16
            tp = f"/tp {bx} ~ {bz}"
            writer.writerow([cx, cz, bx, bz, f"{necessidade:.2f}", f"{macro:.2f}", tamanho, tp])
    print(f"exportado: {os.path.abspath(caminho)}")


# =====================================================================
# 6. EXECUÇÃO
# =====================================================================
if __name__ == "__main__":
    CX_MIN, CX_MAX = -100, 100  # uma célula inteira (3064 chunks de diâmetro)
    CZ_MIN, CZ_MAX = -100, 100

    print("Procurando chunks prováveis (sem confirmar nenhum de verdade)...")
    candidatos = encontrar_provaveis(CX_MIN, CX_MAX, CZ_MIN, CZ_MAX)
    print(f"{len(candidatos)} chunks brutos acima do limiar.")

    representantes = agrupar_provaveis(candidatos)
    print(f"{len(representantes)} montes distintos, após agrupar.\n")

    for cx, cz, necessidade, macro, tamanho in representantes[:30]:
        bx, bz = cx * 16, cz * 16
        print(f"  chunk({cx},{cz})  bloco({bx},{bz})  necessidade={necessidade:6.2f}  "
              f"macro={macro:6.2f}  monte={tamanho:3d} chunks   /tp {bx} ~ {bz}")

    exportar_csv(representantes)
    jpype.shutdownJVM()