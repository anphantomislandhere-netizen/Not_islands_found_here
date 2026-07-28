"""
Detector de ilhas isoladas do End — versão que roda o gerador REAL do jogo
via JPype, com filtro de duas fases pra viabilizar raios gigantes (ex: 256x256 chunks).

FASE 1 (barata): pra cada chunk, pega só os 297 pontos da malha grossa que
`initializeNoiseField` calcula (via reflection, já que é privado) e verifica
se o MAIOR valor entre eles é > 0. Interpolação trilinear é uma média
ponderada com pesos não-negativos somando 1 — o valor interpolado em
qualquer bloco do chunk NUNCA pode passar do maior canto da malha. Se o
maior canto já é <= 0, é garantido que não há end_stone nenhum ali, sem
precisar gerar o chunk inteiro. Isso descarta a esmagadora maioria dos
chunks (que não têm nada) por uma fração do custo.

FASE 2 (cara, só roda nos candidatos): gera o chunk de verdade via
func_180520_a e escaneia bloco a bloco pra achar as posições exatas.
"""

import os
import glob
import jpype
from jpype import JClass
import jpype.imports
from jpype.types import JInt

# =====================================================================
# 1. CONFIGURAÇÃO DE CAMINHOS (ajuste pro seu ambiente)
# =====================================================================
CAMINHO_BIN = r"C:\Users\Pichau\Desktop\wqgrf\bin\minecraft"
PASTA_PROJETO = r"C:\Users\Pichau\Desktop\wqgrf"

# =====================================================================
# 2. CARREGAMENTO DE DEPENDÊNCIAS E JVM
# =====================================================================
jars_encontrados = glob.glob(os.path.join(PASTA_PROJETO, "**", "*.jar"), recursive=True)
classpath_completo = [CAMINHO_BIN] + jars_encontrados

if not jpype.isJVMStarted():
    jpype.startJVM("-Djava.awt.headless=true", classpath=classpath_completo)

try:
    from net.minecraft.init import Bootstrap
    Bootstrap.register()

    from net.minecraft.world.chunk import ChunkPrimer
    from net.minecraft.world.gen import ChunkProviderEnd
    from net.minecraft.init import Blocks
except Exception as e:
    print(f"❌ Erro na etapa de inicialização: {e}")
    jpype.shutdownJVM()
    raise SystemExit(1)

_BLOCO_AR = Blocks.air  # referência canônica real, não amostrada

# =====================================================================
# 3. INSTANCIAR O GERADOR (só uma vez)
# =====================================================================
seed_real = 12
seed_mundo = jpype.JLong(seed_real)
world_obj = None
provider = ChunkProviderEnd(world_obj, seed_mundo)

# =====================================================================
# 4. ACESSO VIA REFLECTION AO initializeNoiseField (privado) — fase 1
# =====================================================================
_Integer = jpype.JClass("java.lang.Integer")
_Object = jpype.JClass("java.lang.Object")
_DoubleArr = jpype.JArray(jpype.JDouble)

_lattice_method = provider.getClass().getDeclaredMethod(
    "initializeNoiseField",
    _DoubleArr,
    jpype.JInt, jpype.JInt, jpype.JInt,
    jpype.JInt, jpype.JInt, jpype.JInt,
)
_lattice_method.setAccessible(True)


def lattice_max(chunk_x: int, chunk_z: int) -> float:
    """Maior valor de densidade nos 297 pontos da malha grossa do chunk —
    sem gerar bloco nenhum. Se isso for <= 0, o chunk é garantidamente vazio."""
    args = jpype.JArray(_Object)(7)
    args[0] = jpype.JObject(None, _DoubleArr)
    ints = (chunk_x * 2, 0, chunk_z * 2, 3, 33, 3)
    for i, v in enumerate(ints):
        args[1 + i] = jpype.JObject(JInt(v), _Integer)
    result = _lattice_method.invoke(provider, args)
    return max(result)


# =====================================================================
# 5. FASE 2 — geração real do chunk, só pros candidatos da fase 1
# =====================================================================
def blocos_do_chunk(chunk_x: int, chunk_z: int, y_min: int, y_max: int):
    """Gera o chunk e devolve (x,y,z) de posições que contêm blocos sólidos.

    HISTÓRICO (pra não repetir os dois erros já cometidos aqui):
    - v1: comparava contra um bloco de referência amostrado em (0,0,0) do
      próprio chunk, assumindo que seria ar — quebrava quando esse chunk
      era um candidato forte com solo sólido bem ali.
    - v2: assumia que `func_180520_a` guarda `null` de verdade pra vazio,
      só porque o código-fonte desse método atribui `null` explicitamente.
      Isso ignora o que `ChunkPrimer.setBlockState` faz com esse `null`
      internamente — e na prática ele nunca devolve `None` de volta (o
      resultado, 4 chunks inteiros "cheios", prova isso: 40960 =
      4 × 16×16×(y_max-y_min)).
    - v3 (esta): compara direto contra `Blocks.air`, a referência canônica
      real do jogo — sem depender de suposição nenhuma sobre como o vazio
      é representado internamente.
    """
    primer = ChunkPrimer()
    provider.func_180520_a(chunk_x, chunk_z, primer)

    for lx in range(16):
        gx = (chunk_x << 4) + lx
        for lz in range(16):
            gz = (chunk_z << 4) + lz
            for y in range(y_min, y_max):
                state = primer.getBlockState(lx, y, lz)
                if state is None:
                    continue
                if state.getBlock() != _BLOCO_AR:
                    yield (gx, y, gz)


def agrupar_blocos(blocos):
    """Agrupa blocos adjacentes (conectividade de 26 vizinhos, 3D) em
    clusters — pra reportar 'ilhas' como formações, não bloco a bloco."""
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

    len_cluster = len(cluster)
    centro = (sum(xs) // len_cluster, sum(ys) // len_cluster, sum(zs) // len_cluster)

    min_p = (min(xs), min(ys), min(zs))
    max_p = (max(xs), max(ys), max(zs))
    bbox = (min_p, max_p)

    dx = max_p[0] - min_p[0] + 1
    dy = max_p[1] - min_p[1] + 1
    dz = max_p[2] - min_p[2] + 1
    dimensoes = (dx, dy, dz)

    volume_bbox = dx * dy * dz
    densidade_pct = round((len_cluster / volume_bbox) * 100, 2)

    return {
        "tamanho": len_cluster,
        "centro": centro,
        "bbox": bbox,
        "dimensoes": dimensoes,
        "volume_bbox": volume_bbox,
        "densidade_pct": densidade_pct,
    }


# =====================================================================
# 6. BUSCA COMPLETA — raio em chunks, nas 3 dimensões
# =====================================================================
def buscar(centro_x: int, centro_z: int, raio_chunks: int, y_min: int, y_max: int):
    from tqdm import tqdm  # pip install tqdm

    chunk_cx, chunk_cz = centro_x >> 4, centro_z >> 4
    total_chunks = (2 * raio_chunks + 1) ** 2
    candidatos = 0
    encontrados = []

    # Raio de exclusão da ilha central (64 chunks = ~1024 blocos do centro)
    RAIO_EXCLUSAO_CHUNKS = 0
    RAIO_EXCLUSAO_QUADRADO = RAIO_EXCLUSAO_CHUNKS ** 2

    coords_chunks = [
        (cx, cz)
        for cx in range(chunk_cx - raio_chunks, chunk_cx + raio_chunks + 1)
        for cz in range(chunk_cz - raio_chunks, chunk_cz + raio_chunks + 1)
    ]

    barra = tqdm(coords_chunks, unit="chunk", desc="varrendo")
    for cx, cz in barra:
        # -------------------------------------------------------------
        # FILTRO ZERO: Ignora a ilha principal no centro (0,0)
        # -------------------------------------------------------------
        #if (cx**2 + cz**2) <= RAIO_EXCLUSAO_QUADRADO:
        #    continue

        if lattice_max(cx, cz) <= 0.0:
            continue  # fase 1: garantidamente vazio, pula sem gerar o chunk
            
        candidatos += 1
        for x, y, z in blocos_do_chunk(cx, cz, y_min, y_max):
            encontrados.append((x, y, z))
        barra.set_postfix(candidatos=candidatos, blocos=len(encontrados))

    print(f"chunks totais: {total_chunks} | candidatos após filtro: {candidatos} "
          f"({100*candidatos/total_chunks:.2f}%) | blocos encontrados: {len(encontrados)}")
    return encontrados


# =====================================================================
# 7. EXECUÇÃO
# =====================================================================
if __name__ == "__main__":
    centro_x, centro_z = -17792, -21120
    raio_chunks = 50
    y_min, y_max = 40, 80

    print(f"🔍 Buscando num raio de {raio_chunks} chunks ao redor de X={centro_x}, Z={centro_z}...")
    resultados = buscar(centro_x, centro_z, raio_chunks, y_min, y_max)

    clusters = agrupar_blocos(resultados)
    clusters.sort(key=len, reverse=True)  # maiores (mais confiáveis) primeiro

    print(f"\n{len(clusters)} aglomerados encontrados:\n")
    for c in clusters:
        info = resumo_cluster(c)
        aviso = "  ⚠️  bloco isolado — vale conferir no jogo antes de confiar" if info["tamanho"] == 1 else ""
        print(f"  🪨 Blocos Reais : {info['tamanho']} m³")
        print(f"  📍 Centro        : {info['centro']}")
        print(f"  📦 Bounding Box  : {info['bbox']}")
        print(f"  📐 Dimensões     : {info['dimensoes'][0]}x{info['dimensoes'][1]}x{info['dimensoes'][2]} (LxAxP)")
        print(f"  📊 Preenchimento : {info['densidade_pct']}% da caixa{aviso}\n")

    jpype.shutdownJVM()