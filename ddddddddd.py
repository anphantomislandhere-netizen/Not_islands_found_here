"""
Detector de ilhas do End — com mapa de calor de duas métricas lado a lado.

IMPORTANTE, leia antes de rodar: a métrica "necessidade"/"macro" (soma de
oitavas por reflection) ainda NÃO foi validada contra a checagem real
(lattice_max) em nenhuma coordenada confirmada — foi assim que achamos o
bug de candidatos reais sendo excluídos. O mapa de calor mostra as duas
métricas lado a lado exatamente para você conseguir ver ONDE elas
discordam, olhando pra uma ilha que você já confirmou no jogo. Até isso
ser resolvido, confie em `lattice_max` (painel esquerdo) como verdade —
o painel direito é o suspeito sendo investigado, não a fonte da verdade.

Funcionalidades incluídas (tudo que foi útil até aqui):
  - filtro de duas fases (barato antes de caro)
  - clusterização de blocos (3D) e de chunks candidatos (2D)
  - lista priorizada de 10 réplicas periódicas
  - medição de deriva real entre célula base e réplicas
  - barra de progresso
  - mapa de calor com overlay de ilhas conhecidas (pra debug visual)
  - exportação da grade bruta em CSV, pra reanalisar sem rodar de novo
  - multiprocessing (uma JVM por processo) na fase de varredura
  - 4 ramos periódicos gerados automaticamente, cada um em arquivo isolado
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

PERIODO_CHUNKS = 3064

ILHAS_CONHECIDAS = [
    (6080, 23680, "ilha confirmada 1"),
]

jars_encontrados = glob.glob(os.path.join(PASTA_PROJETO, "**", "*.jar"), recursive=True)
classpath_completo = [CAMINHO_BIN] + jars_encontrados

if not jpype.isJVMStarted():
    jpype.startJVM("-Djava.awt.headless=true", classpath=classpath_completo)

from net.minecraft.init import Bootstrap
Bootstrap.register()
from net.minecraft.world.chunk import ChunkPrimer
from net.minecraft.world.gen import ChunkProviderEnd
from net.minecraft.init import Blocks

_BLOCO_AR = Blocks.air
provider = ChunkProviderEnd(None, jpype.JLong(SEED))

# =====================================================================
# 2. lattice_max — checagem REAL e completa (confiável)
# =====================================================================
_Integer = jpype.JClass("java.lang.Integer")
_Object = jpype.JClass("java.lang.Object")
_DoubleArr = jpype.JArray(jpype.JDouble)

_lattice_method = provider.getClass().getDeclaredMethod(
    "initializeNoiseField", _DoubleArr,
    jpype.JInt, jpype.JInt, jpype.JInt, jpype.JInt, jpype.JInt, jpype.JInt,
)
_lattice_method.setAccessible(True)


def lattice_max(cx: int, cz: int) -> float:
    args = jpype.JArray(_Object)(7)
    args[0] = jpype.JObject(None, _DoubleArr)
    for i, v in enumerate((cx * 2, 0, cz * 2, 3, 33, 3)):
        args[1 + i] = jpype.JObject(JInt(v), _Integer)
    return max(_lattice_method.invoke(provider, args))


# =====================================================================
# 3. necessidade/macro — métrica NOVA, ainda sob suspeita
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
TODAS_OITAVAS = set(range(16))
LIMIAR_MACRO_PADRAO = 110.0
LIMIAR_NECESSIDADE_PADRAO = 108.0

OFFSETS_REPLICA = [
    (0, 1), (1, 0), (0, -1), (-1, 0),
    (1, 1), (-1, -1), (1, -1), (-1, 1),
    (0, 2), (2, 0),
]


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
    x_off, z_off = cx * 2, cz * 2
    total1, sub1 = somar_oitavas_com_subtotal(_colecao_noiseGen1, indices_oitavas, x_off, z_off)
    total2, sub2 = somar_oitavas_com_subtotal(_colecao_noiseGen2, indices_oitavas, x_off, z_off)
    necessidade = max(max(total1), max(total2)) / 512.0
    macro = max(max(sub1), max(sub2)) / 512.0
    return macro, necessidade


# =====================================================================
# 4. BLOCOS E CLUSTERS (3D)
# =====================================================================
def blocos_do_chunk(chunk_x: int, chunk_z: int, y_min: int, y_max: int):
    primer = ChunkPrimer()
    provider.func_180520_a(chunk_x, chunk_z, primer)
    for lx in range(16):
        gx = (chunk_x << 4) + lx
        for lz in range(16):
            gz = (chunk_z << 4) + lz
            for y in range(y_min, y_max):
                state = primer.getBlockState(lx, y, lz)
                if state is not None and state.getBlock() != _BLOCO_AR:
                    yield (gx, y, gz)


def agrupar_blocos(blocos):
    posicoes = set(blocos)
    visitados = set()
    clusters = []
    offsets = [(dx, dy, dz) for dx in (-1, 0, 1) for dy in (-1, 0, 1)
               for dz in (-1, 0, 1) if (dx, dy, dz) != (0, 0, 0)]
    for inicio in blocos:
        if inicio in visitados:
            continue
        pilha = [inicio]
        visitados.add(inicio)
        cluster = []
        while pilha:
            atual = pilha.pop()
            cluster.append(atual)
            cx, cy, cz = atual
            for dx, dy, dz in offsets:
                viz = (cx + dx, cy + dy, cz + dz)
                if viz in posicoes and viz not in visitados:
                    visitados.add(viz)
                    pilha.append(viz)
        clusters.append(cluster)
    return clusters


def resumo_cluster(cluster):
    xs = [p[0] for p in cluster]
    ys = [p[1] for p in cluster]
    zs = [p[2] for p in cluster]
    n = len(cluster)
    centro = (sum(xs) // n, sum(ys) // n, sum(zs) // n)
    min_p, max_p = (min(xs), min(ys), min(zs)), (max(xs), max(ys), max(zs))
    dx, dy, dz = (max_p[0]-min_p[0]+1, max_p[1]-min_p[1]+1, max_p[2]-min_p[2]+1)
    volume = dx * dy * dz
    return {
        "tamanho": n, "centro": centro, "bbox": (min_p, max_p),
        "dimensoes": (dx, dy, dz), "volume_bbox": volume,
        "densidade_pct": round(n / volume * 100, 2),
    }


# =====================================================================
# 5. CLUSTERIZAÇÃO DE CHUNKS CANDIDATOS (2D)
# =====================================================================
def agrupar_chunks(candidatos, raio_adjacencia=1):
    dados = {(cx, cz): (score, necessidade, motivo)
             for cx, cz, score, necessidade, motivo in candidatos}
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
        melhor = max(grupo, key=lambda p: max(dados[p][0], dados[p][1]))
        score, necessidade, motivo = dados[melhor]
        representantes.append((melhor[0], melhor[1], score, necessidade, motivo, len(grupo)))
    representantes.sort(key=lambda t: max(t[2], t[3]), reverse=True)
    return representantes


# =====================================================================
# 6. RÉPLICAS E DERIVA
# =====================================================================
def testar_replicas(cx, cz, offsets=OFFSETS_REPLICA, y_min=30, y_max=70):
    achados = []
    for n, m in offsets:
        rcx, rcz = cx + PERIODO_CHUNKS * n, cz + PERIODO_CHUNKS * m
        if lattice_max(rcx, rcz) > 0.0:
            blocos = list(blocos_do_chunk(rcx, rcz, y_min, y_max))
            if blocos:
                achados.append({"n": n, "m": m, "chunk": (rcx, rcz), "blocos": len(blocos)})
    return achados


def medir_deriva(cx, cz, offsets=OFFSETS_REPLICA):
    base = macro_e_necessidade(cx, cz, TODAS_OITAVAS)[1]
    resultado = {"base": base, "replicas": []}
    for n, m in offsets:
        rcx, rcz = cx + PERIODO_CHUNKS * n, cz + PERIODO_CHUNKS * m
        valor = macro_e_necessidade(rcx, rcz, TODAS_OITAVAS)[1]
        resultado["replicas"].append({"n": n, "m": m, "valor": valor, "diferenca": valor - base})
    return resultado


# =====================================================================
# 7. VARREDURA PARA O MAPA DE CALOR (com multiprocessing)
# =====================================================================
def processar_lote_mapa(lote):
    resultados = []
    for cx, cz in lote:
        lm = lattice_max(cx, cz)
        macro, necessidade = macro_e_necessidade(cx, cz)
        resultados.append((cx, cz, lm, necessidade, macro))
    return resultados


def gerar_lotes(cx_min, cx_max, cz_min, cz_max, tamanho_lote=500):
    lote = []
    for cx in range(cx_min, cx_max + 1):
        for cz in range(cz_min, cz_max + 1):
            lote.append((cx, cz))
            if len(lote) >= tamanho_lote:
                yield lote
                lote = []
    if lote:
        yield lote


def varrer_para_mapa(cx_min, cx_max, cz_min, cz_max, n_processos=None, tamanho_lote=500):
    import multiprocessing as mp
    from tqdm import tqdm
    import numpy as np

    largura = cx_max - cx_min + 1
    altura = cz_max - cz_min + 1
    grade_lattice = np.zeros((largura, altura))
    grade_necessidade = np.zeros((largura, altura))
    grade_macro = np.zeros((largura, altura))

    total_chunks = largura * altura
    total_lotes = -(-total_chunks // tamanho_lote)
    lotes = gerar_lotes(cx_min, cx_max, cz_min, cz_max, tamanho_lote)

    with mp.Pool(processes=n_processos) as pool:
        barra = tqdm(pool.imap_unordered(processar_lote_mapa, lotes), total=total_lotes,
                     desc="mapa de calor", unit="lote")
        for resultado_lote in barra:
            for cx, cz, lm, necessidade, macro in resultado_lote:
                i, j = cx - cx_min, cz - cz_min
                grade_lattice[i, j] = lm
                grade_necessidade[i, j] = necessidade
                grade_macro[i, j] = macro

    return grade_lattice, grade_necessidade, grade_macro


# =====================================================================
# 8. MAPA DE CALOR
# =====================================================================
def gerar_mapa_calor(grade_lattice, grade_necessidade, cx_min, cz_min,
                      ilhas_conhecidas=None, caminho_saida="mapa_calor.png"):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np
    from matplotlib.colors import Normalize, to_rgba

    print(f"grade_lattice: min={grade_lattice.min():.2f} max={grade_lattice.max():.2f} "
          f"média={grade_lattice.mean():.2f}")
    print(f"grade_necessidade: min={grade_necessidade.min():.2f} max={grade_necessidade.max():.2f} "
          f"média={grade_necessidade.mean():.2f}")
    if grade_lattice.min() == 0.0 and grade_lattice.max() == 0.0:
        print("⚠️  grade_lattice está TODA ZERO — a varredura provavelmente não "
              "preencheu nada. O mapa vai sair, mas vai ser uma cor sólida sem informação.")

    largura, altura = grade_lattice.shape
    fig, axes = plt.subplots(1, 2, figsize=(18, 8))

    im0 = axes[0].imshow(grade_lattice.T, origin="lower", cmap="RdYlGn",
                          extent=[cx_min, cx_min + largura, cz_min, cz_min + altura],
                          vmin=-100, vmax=50)
    axes[0].set_title("lattice_max — checagem REAL (confiável)")
    axes[0].set_xlabel("chunk X")
    axes[0].set_ylabel("chunk Z")
    fig.colorbar(im0, ax=axes[0], label="densidade máxima (>0 = tem bloco)")

    im1 = axes[1].imshow(grade_necessidade.T, origin="lower", cmap="RdYlGn",
                          extent=[cx_min, cx_min + largura, cz_min, cz_min + altura],
                          vmin=0, vmax=140)
    axes[1].set_title("necessidade — métrica NOVA (sob suspeita)")
    axes[1].set_xlabel("chunk X")
    axes[1].set_ylabel("chunk Z")
    fig.colorbar(im1, ax=axes[1], label="valor bruto (limiar ~108)")

    if ilhas_conhecidas:
        for x, z, nome in ilhas_conhecidas:
            ccx, ccz = x >> 4, z >> 4
            for ax in axes:
                ax.scatter([ccx], [ccz], marker="x", s=150, c="black", linewidths=3)
                ax.annotate(nome, (ccx, ccz), color="black", fontsize=8,
                            xytext=(5, 5), textcoords="offset points")

    plt.tight_layout()
    caminho_absoluto = os.path.abspath(caminho_saida)
    try:
        plt.savefig(caminho_absoluto, dpi=150)
    except Exception as e:
        print(f"❌ ERRO ao salvar o mapa: {e}")
        raise
    existe = os.path.exists(caminho_absoluto)
    tamanho = os.path.getsize(caminho_absoluto) if existe else 0
    print(f"mapa salvo em: {caminho_absoluto}")
    print(f"arquivo existe? {existe}  |  tamanho: {tamanho} bytes"
          + ("  ⚠️ arquivo vazio/suspeito" if tamanho < 1000 else ""))
    plt.close(fig)


def exportar_csv(grade_lattice, grade_necessidade, grade_macro, cx_min, cz_min, caminho="grade.csv"):
    largura, altura = grade_lattice.shape
    with open(caminho, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["chunk_x", "chunk_z", "lattice_max", "necessidade", "macro"])
        for i in range(largura):
            for j in range(altura):
                writer.writerow([cx_min + i, cz_min + j,
                                  f"{grade_lattice[i,j]:.4f}",
                                  f"{grade_necessidade[i,j]:.4f}",
                                  f"{grade_macro[i,j]:.4f}"])
    print(f"grade exportada em {caminho}")


# =====================================================================
# 8b. MAPA DE RESÍDUO — contribuição isolada das oitavas não-periódicas
# =====================================================================
def gerar_mapa_residuo(grade_residuo, cx_min, cz_min, ilhas_conhecidas=None,
                        caminho_saida="mapa_residuo.png"):
    """grade_residuo = necessidade - macro, célula a célula. Vermelho =
    contribuição das oitavas não-periódicas mais negativa (atrapalhou);
    verde = mais positiva (fez o trabalho pesado sozinha)."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    print(f"grade_residuo: min={grade_residuo.min():.2f} max={grade_residuo.max():.2f} "
          f"média={grade_residuo.mean():.2f}")

    largura, altura = grade_residuo.shape
    limite = max(abs(grade_residuo.min()), abs(grade_residuo.max()), 1.0)  # simétrico em torno de 0

    fig, ax = plt.subplots(figsize=(11, 9))
    im = ax.imshow(grade_residuo.T, origin="lower", cmap="RdYlGn",
                    extent=[cx_min, cx_min + largura, cz_min, cz_min + altura],
                    vmin=-limite, vmax=limite)
    ax.set_title("resíduo (necessidade − macro) — oitavas NÃO-periódicas")
    ax.set_xlabel("chunk X")
    ax.set_ylabel("chunk Z")
    fig.colorbar(im, ax=ax, label="vermelho = mais negativo   |   verde = mais positivo")

    if ilhas_conhecidas:
        for x, z, nome in ilhas_conhecidas:
            ccx, ccz = x >> 4, z >> 4
            ax.scatter([ccx], [ccz], marker="x", s=150, c="black", linewidths=3)
            ax.annotate(nome, (ccx, ccz), color="black", fontsize=8,
                        xytext=(5, 5), textcoords="offset points")

    plt.tight_layout()
    caminho_absoluto = os.path.abspath(caminho_saida)
    plt.savefig(caminho_absoluto, dpi=150)
    print(f"mapa de resíduo salvo em: {caminho_absoluto}")
    plt.close(fig)


OITAVAS_SECUNDARIAS = TODAS_OITAVAS - OITAVAS_DOMINANTES  # as 14 não-dominantes


def somar_oitavas_simples(colecao_oitavas, indices, x_off, z_off):
    """Soma só as oitavas em `indices` — mais simples que
    somar_oitavas_com_subtotal porque não precisa rastrear total E
    subtotal juntos, só o que interessa."""
    tamanho = LATTICE_X * LATTICE_Y * LATTICE_Z
    total = jpype.JArray(jpype.JDouble)(tamanho)
    amp = 1.0
    for oitava in range(16):
        if oitava in indices:
            base_x = _wrap_java(x_off * amp * SCALE_X)
            base_z = _wrap_java(z_off * amp * SCALE_Z)
            colecao_oitavas[oitava].populateNoiseArray(
                total, JDouble(base_x), JDouble(0.0), JDouble(base_z),
                JInt(LATTICE_X), JInt(LATTICE_Y), JInt(LATTICE_Z),
                JDouble(SCALE_X * amp), JDouble(SCALE_Y * amp), JDouble(SCALE_Z * amp),
                JDouble(amp),
            )
        amp /= 2.0
    return list(total)


def residuo_direto(cx: int, cz: int, indices_secundarias=OITAVAS_SECUNDARIAS) -> float:
    """Ruído secundário puro — só as oitavas NÃO-dominantes, sem nunca
    calcular necessidade nem macro. Bem definido por si só (diferente do
    'necessidade - macro' antigo, que comparava dois máximos que podiam
    vir de pontos ou geradores diferentes)."""
    x_off, z_off = cx * 2, cz * 2
    t1 = somar_oitavas_simples(_colecao_noiseGen1, indices_secundarias, x_off, z_off)
    t2 = somar_oitavas_simples(_colecao_noiseGen2, indices_secundarias, x_off, z_off)
    return max(max(t1), max(t2)) / 512.0


def processar_lote_residuo(lote, indices_secundarias=OITAVAS_SECUNDARIAS):
    return [(cx, cz, residuo_direto(cx, cz, indices_secundarias)) for cx, cz in lote]


def varrer_residuo(cx_min, cx_max, cz_min, cz_max, n_processos=8, tamanho_lote=500):
    """Varredura dedicada só pro secundário — bem mais barata que
    varrer_para_mapa, porque nunca chama lattice_max (a checagem cara,
    via reflection num método privado) nem precisa da soma das oitavas
    dominantes."""
    import multiprocessing as mp
    from tqdm import tqdm
    import numpy as np

    largura = cx_max - cx_min + 1
    altura = cz_max - cz_min + 1
    grade = np.zeros((largura, altura))

    total_chunks = largura * altura
    total_lotes = -(-total_chunks // tamanho_lote)
    lotes = gerar_lotes(cx_min, cx_max, cz_min, cz_max, tamanho_lote)

    with mp.Pool(processes=n_processos) as pool:
        barra = tqdm(pool.imap_unordered(processar_lote_residuo, lotes), total=total_lotes,
                     desc="secundário", unit="lote")
        for resultado_lote in barra:
            for cx, cz, valor in resultado_lote:
                i, j = cx - cx_min, cz - cz_min
                grade[i, j] = valor

    return grade


def mapa_residuo_para_coordenada(x_bloco, z_bloco, raio_chunks=150, nome="ponto",
                                  caminho_saida="mapa_residuo.png", n_processos=8):
    """Digite a coordenada em blocos — devolve o mapa do ruído secundário
    (só as 14 oitavas não-dominantes) num raio (em chunks) ao redor dela."""
    cx_centro, cz_centro = x_bloco >> 4, z_bloco >> 4
    grade_residuo = varrer_residuo(
        cx_centro - raio_chunks, cx_centro + raio_chunks,
        cz_centro - raio_chunks, cz_centro + raio_chunks,
        n_processos=n_processos,
    )
    gerar_mapa_residuo(grade_residuo, cx_centro - raio_chunks, cz_centro - raio_chunks,
                        ilhas_conhecidas=[(x_bloco, z_bloco, nome)], caminho_saida=caminho_saida)
    return grade_residuo


# =====================================================================
# 9. EXECUÇÃO
# =====================================================================
if __name__ == "__main__":
    X_BASE, Z_BASE, NOME_BASE = ILHAS_CONHECIDAS[0]
    RAIO = 150

    DESLOCAMENTO_BLOCOS = PERIODO_CHUNKS * 16  # 3064 chunks * 16 = 49024 blocos

    ramos = [
        (X_BASE, Z_BASE),
        (X_BASE + DESLOCAMENTO_BLOCOS, Z_BASE),
        (X_BASE, Z_BASE + DESLOCAMENTO_BLOCOS),
        (X_BASE + DESLOCAMENTO_BLOCOS, Z_BASE + DESLOCAMENTO_BLOCOS),
    ]

    for indice, (x_ramo, z_ramo) in enumerate(ramos, start=1):
        cx_centro, cz_centro = x_ramo >> 4, z_ramo >> 4
        print(f"\n=== Ramo {indice}: bloco ({x_ramo}, {z_ramo}) — chunk ({cx_centro}, {cz_centro}) ===")

        # ✅ CORRIGIDO: Chamando a varredura dedicada só do secundário (sem lattice_max)
        grade_residuo = varrer_residuo(
            cx_centro - RAIO, cx_centro + RAIO, cz_centro - RAIO, cz_centro + RAIO,
            n_processos=8,
        )

        marcador = [(x_ramo, z_ramo, NOME_BASE)]
        
        # ✅ CORRIGIDO: Gerando o mapa usando a função específica de resíduo
        gerar_mapa_residuo(
            grade_residuo, cx_centro - RAIO, cz_centro - RAIO,
            ilhas_conhecidas=marcador, caminho_saida=f"mapa_secundario_{indice}.png"
        )

    jpype.shutdownJVM()