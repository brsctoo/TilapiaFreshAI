"""Verificações do pipeline de pré-processamento com dados sintéticos.

Executa o código das células do notebook (exceto a que roda sobre os ZIPs do Drive)
e confere os cenários de inventário, revisão e exportação.

Uso: python tests/verificar_pipeline.py [caminho/do/notebook.ipynb]
Requer numpy, pandas, pillow e scipy.
"""
import io
import json
import sys
import tempfile
import zipfile
from pathlib import Path

import numpy as np
import pandas as pd
from PIL import Image

NOTEBOOK = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).parents[1] / 'notebooks' / 'TilapiaFreshAI.ipynb'


def carregar_pipeline():
    nb = json.loads(NOTEBOOK.read_text(encoding='utf-8'))
    ns = {'__name__': 'pipeline'}
    for cell in nb['cells']:
        codigo = ''.join(cell['source'])
        if cell['cell_type'] == 'code' and not codigo.startswith('# Ajuste ZIP_JPG'):
            exec(compile(codigo, str(NOTEBOOK), 'exec'), ns)
    return ns


p = carregar_pipeline()
tmp = Path(tempfile.mkdtemp(prefix='tilapia_verif_'))


def png(arr):
    b = io.BytesIO()
    Image.fromarray(arr).save(b, 'PNG')
    return b.getvalue()


def segmentada(seed):
    """Filé sobre fundo preto, como nas imagens dos DOCX."""
    rng = np.random.default_rng(seed)
    a = np.zeros((40, 50, 3), np.uint8)
    a[5:35, 8:42] = rng.integers(50, 230, (30, 34, 3), dtype=np.uint8)
    return png(a)


def crua(seed):
    """Foto JPG crua: pano claro o bastante para passar no limiar, filé no centro."""
    rng = np.random.default_rng(seed)
    a = np.full((40, 50, 3), 80, np.uint8)
    a[10:30, 15:35] = rng.integers(150, 230, (20, 20, 3), dtype=np.uint8)
    return png(a)


def docx(img):
    b = io.BytesIO()
    with zipfile.ZipFile(b, 'w') as z:
        z.writestr('word/media/image1.png', img)
    return b.getvalue()


def zips(nome, jpg_entradas, docx_entradas):
    pasta = tmp / nome
    pasta.mkdir()
    jpg, doc = pasta / 'jpg.zip', pasta / 'docx.zip'
    with zipfile.ZipFile(jpg, 'w') as z:
        for arq, dados in jpg_entradas.items():
            z.writestr(arq, dados)
    with zipfile.ZipFile(doc, 'w') as z:
        for arq, dados in docx_entradas.items():
            z.writestr(arq, dados)
    return pasta, {'jpg': jpg, 'docx': doc}


def ler_rev(out):
    return pd.read_csv(out / 'revisoes.csv', dtype=str, keep_default_na=False)


def gravar_rev(out, rev):
    rev.to_csv(out / 'revisoes.csv', index=False)


cenarios = []


def cenario(func):
    func()
    cenarios.append(func.__doc__)
    return func


@cenario
def fluxo_completo():
    """ZIP/DOCX, arquivo corrompido, hífen extra, prioridade do segmentado, aprovação e partição"""
    jpg = {f'Dia {d}/C1 - D{d} - SEMO - 0_.jpg': crua(d) for d in range(11)}
    jpg['aux.xls'] = b'aux'
    doc = {f'Fotos/C2 - D{d} - SEMO - 0_.docx': docx(segmentada(100 + d)) for d in range(11)}
    doc['C2 - D1 - OA - -8_.docx'] = docx(segmentada(999))
    doc['C1 - D0 - SEMO - 0_.docx'] = docx(segmentada(0))
    doc['C1 - D3 - OA - 04_.docx'] = b'corrompido'
    pasta, args = zips('fluxo', jpg, doc)
    out = pasta / 'saida'
    p['CAMPOS_ID_FILE'] = None
    r1 = p['executar_pipeline'](args, out)
    assert r1['chaves_unicas'] == 23, r1
    assert r1['recuperadas_docx'] == 12, r1  # chaves que só existem no DOCX
    assert r1['chaves_por_fonte'] == {'docx': 12, 'docx+jpg': 1, 'jpg': 10}, r1['chaves_por_fonte']
    assert not (out / 'atual' / 'revisoes.csv').exists()  # um único revisoes.csv editável
    assert r1['inventario_status']['rejeitado'] == 1
    assert r1['features_finais'] == 0
    falhas = pd.read_csv(out / 'atual' / 'falhas_roi.csv')
    sem_mascara = falhas[falhas.erro.str.contains('foto_nao_segmentada_requer_mascara_manual')]
    assert len(sem_mascara) == 11, falhas.erro.tolist()  # todos os JPG, nenhum com máscara manual
    primeiro = pd.read_csv(out / 'atual' / 'features_preliminares.csv')
    assert 'hsv_h_media' not in primeiro.columns and 'hue_media_circular' in primeiro.columns
    r2 = p['executar_pipeline'](args, out)
    assert r1 == r2
    pd.testing.assert_frame_equal(primeiro, pd.read_csv(out / 'atual' / 'features_preliminares.csv'))
    rev = ler_rev(out)
    rev['aprovado'] = 'sim'
    gravar_rev(out, rev)
    p['CAMPOS_ID_FILE'] = ['c']
    r3 = p['executar_pipeline'](args, out)
    p['CAMPOS_ID_FILE'] = None
    assert r3['features_finais'] == 13 and r3['identidade_file_completa'], r3
    final = pd.read_csv(out / 'atual' / 'features_final.csv', dtype={'fillet_id': str})
    # C1 só tem D0 aprovado (os demais dias são JPG sem máscara): a partição precisa recusar.
    try:
        p['preparar_particao'](final, 'SEMO', ['1'])
        raise AssertionError('partição sem D0–D10 aceita')
    except ValueError as exc:
        assert 'D0–D10' in str(exc)


@cenario
def aprovacao_nao_herda_falha():
    """Execução que falha depois dos recortes não deixa revisoes.csv à frente das pranchas"""
    doc = {f'C1 - D{d} - SEMO - 0_.docx': docx(segmentada(d)) for d in range(3)}
    pasta, args = zips('falha', {}, doc)
    out = pasta / 'saida'
    p['executar_pipeline'](args, out)
    antes = (out / 'revisoes.csv').read_bytes()
    original = p['gerar_pranchas']
    p['LIMIAR_FUNDO'] = 120

    def quebra(*a, **k):
        raise OSError('falha simulada no Drive')

    p['gerar_pranchas'] = quebra
    try:
        p['executar_pipeline'](args, out)
        raise AssertionError('a execução deveria falhar')
    except OSError:
        pass
    finally:
        p['gerar_pranchas'] = original
    assert (out / 'revisoes.csv').read_bytes() == antes
    rev = ler_rev(out)
    rev['aprovado'] = 'sim'  # aprovado olhando as pranchas antigas
    gravar_rev(out, rev)
    r = p['executar_pipeline'](args, out)
    p['LIMIAR_FUNDO'] = 30
    assert r['features_finais'] == 0, r  # recortes novos voltam a pendente


@cenario
def opcoes_normalizadas():
    """'Sim', ' SIM ' e 'Não' são aceitos; valores desconhecidos interrompem a execução"""
    doc = {f'C1 - D{d} - SEMO - 0_.docx': docx(segmentada(d)) for d in range(3)}
    pasta, args = zips('opcoes', {}, doc)
    out = pasta / 'saida'
    p['executar_pipeline'](args, out)
    rev = ler_rev(out)
    rev['aprovado'] = ['Sim', ' SIM ', 'Não']
    gravar_rev(out, rev)
    r = p['executar_pipeline'](args, out)
    assert r['features_finais'] == 2, r
    rev = ler_rev(out)
    rev.loc[0, 'usar'] = 's'
    gravar_rev(out, rev)
    try:
        p['executar_pipeline'](args, out)
        raise AssertionError('valor desconhecido aceito')
    except ValueError as exc:
        assert 'usar' in str(exc)


@cenario
def revisoes_preservadas():
    """Revisão de candidato ausente numa execução fica guardada e volta com ele"""
    base = {f'C1 - D{d} - SEMO - 0_.docx': docx(segmentada(d)) for d in range(3)}
    pasta, args = zips('preserva', {}, base)
    out = pasta / 'saida'
    p['executar_pipeline'](args, out)
    rev = ler_rev(out)
    alvo = rev.loc[rev.image_key == 'C1_D2_SEMO_0', 'candidate_id'].item()
    rev.loc[rev.candidate_id == alvo, ['aprovado', 'observacao']] = ['sim', 'conferido']
    gravar_rev(out, rev)
    parcial = tmp / 'preserva' / 'docx_parcial.zip'
    with zipfile.ZipFile(parcial, 'w') as z:
        for arq in list(base)[:2]:
            z.writestr(arq, base[arq])
    p['executar_pipeline']({'jpg': args['jpg'], 'docx': parcial}, out)
    linha = ler_rev(out).set_index('candidate_id').loc[alvo]
    assert (linha.ativo, linha.observacao) == ('nao', 'conferido'), linha.to_dict()
    p['executar_pipeline'](args, out)
    linha = ler_rev(out).set_index('candidate_id').loc[alvo]
    assert (linha.ativo, linha.observacao) == ('sim', 'conferido'), linha.to_dict()


@cenario
def mascara_manual_e_novo_candidato():
    """JPG com máscara manual relativa à pasta de saída; DOCX novo não toma a chave já escolhida"""
    pasta, args = zips('manual', {'C1 - D0 - SEMO - 0_.jpg': crua(7)}, {})
    out = pasta / 'outra_saida'
    p['executar_pipeline'](args, out)
    mascara = np.zeros((40, 50), np.uint8)
    mascara[10:30, 15:35] = 255
    (out / 'mascaras').mkdir()
    Image.fromarray(mascara).save(out / 'mascaras' / 'c1_d0.png')
    rev = ler_rev(out)
    rev[['mascara_manual']] = 'mascaras/c1_d0.png'
    gravar_rev(out, rev)
    p['executar_pipeline'](args, out)  # PASTA_SAIDA global aponta para outro lugar
    rev = ler_rev(out)
    rev['aprovado'] = 'sim'
    gravar_rev(out, rev)
    r = p['executar_pipeline'](args, out)
    assert r['features_finais'] == 1 and r['roi_falhas'] == 0, r
    prelim = pd.read_csv(out / 'atual' / 'features_preliminares.csv')
    assert prelim.roi_pixels.item() == 400
    with zipfile.ZipFile(args['docx'], 'w') as z:
        z.writestr('C1 - D0 - SEMO - 0_.docx', docx(segmentada(7)))
    r = p['executar_pipeline'](args, out)
    assert not r['conflitos_chave'] and r['features_finais'] == 1, r
    final = pd.read_csv(out / 'atual' / 'features_final.csv')
    assert final.origem.item() == 'jpg'


@cenario
def janela_antes_do_maior_componente():
    """A janela da revisão escolhe um filé menor que outro objeto da imagem"""
    a = np.zeros((60, 80, 3), np.uint8)
    a[5:55, 5:45] = 200   # objeto grande (por exemplo, etiqueta)
    a[20:30, 55:75] = 120  # filé
    rgb = a
    mascara, bbox = p['aplicar_recorte'](rgb, {'x0': '50', 'y0': '15', 'x1': '80', 'y1': '35'}, 'docx', tmp)
    assert bbox == (55, 20, 75, 30) and mascara.sum() == 200, bbox


@cenario
def matiz_circular():
    """Matiz em torno do vermelho tem média circular perto de 0, sem estatísticas lineares"""
    a = np.zeros((20, 20, 3), np.uint8)
    a[:, :10] = (230, 120, 140)  # rosado, matiz perto de 0,97
    a[:, 10:] = (230, 140, 120)  # alaranjado, matiz perto de 0,03
    f = p['extrair_features'](a, np.ones((20, 20), bool))
    assert 'hsv_h_media' not in f
    assert min(f['hue_media_circular'], 1 - f['hue_media_circular']) < 0.01, f['hue_media_circular']
    assert f['hue_fracao_cromatica'] == 1.0


@cenario
def resolver_zip_ignora_pasta():
    """Pasta extraída com o mesmo nome do ZIP não impede encontrar o ZIP"""
    d = tmp / 'dataset'
    (d / 'dataset_tilapias_compactado').mkdir(parents=True)
    (d / 'dataset_tilapias_compactado.zip').write_bytes(b'')
    assert p['resolver_zip'](d, 'dataset_tilapias_compactado').suffix == '.zip'


print(json.dumps({'resultado': 'OK', 'cenarios': cenarios}, ensure_ascii=False, indent=2))
