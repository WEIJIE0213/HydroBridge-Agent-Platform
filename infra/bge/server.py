"""Single GPU owner for official BGE-M3 dense and BGE reranker inference."""
import json
import os
from contextlib import asynccontextmanager
from pathlib import Path
from threading import Lock

import torch
import torch.nn.functional as F
from fastapi import FastAPI
from pydantic import BaseModel, Field
from transformers import AutoModel, AutoModelForSequenceClassification, AutoTokenizer

DEVICE = os.environ.get('BGE_DEVICE', 'cuda')
MAX_LENGTH = int(os.environ.get('BGE_MAX_LENGTH', '1024'))
BATCH_SIZE = int(os.environ.get('BGE_BATCH_SIZE', '8'))
lock = Lock()
models = {}

@asynccontextmanager
async def lifespan(app):
    if DEVICE == 'cuda' and not torch.cuda.is_available():
        raise RuntimeError('BGE requires CUDA; refusing a silent CPU/hash fallback')
    torch.set_num_threads(2)
    for kind, name, cls in (
        ('embedding', 'bge-m3', AutoModel),
        ('reranker', 'bge-reranker-v2-m3', AutoModelForSequenceClassification),
    ):
        path = '/models/' + name
        tokenizer = AutoTokenizer.from_pretrained(path, local_files_only=True)
        model = cls.from_pretrained(path, local_files_only=True,
            torch_dtype=torch.float16 if DEVICE == 'cuda' else torch.float32,
            attn_implementation='sdpa').to(DEVICE).eval()
        models[kind] = (tokenizer, model)
    yield
    models.clear()

app = FastAPI(lifespan=lifespan)

class EmbeddingRequest(BaseModel):
    texts: list[str] = Field(min_length=1, max_length=128)

class RerankRequest(BaseModel):
    query: str = Field(min_length=1, max_length=30000)
    passages: list[str] = Field(max_length=200)

@app.get('/health')
def health():
    return {'status':'ready', 'device':DEVICE, 'embedding_model':'BAAI/bge-m3',
        'reranker_model':'BAAI/bge-reranker-v2-m3', 'dimension':1024,
        'max_length':MAX_LENGTH, 'batch_size':BATCH_SIZE,
        'manifest': json.loads(Path('/models/manifest.json').read_text())}

@app.post('/embed')
def embed(request: EmbeddingRequest):
    tokenizer, model = models['embedding']
    output = []
    with lock, torch.inference_mode():
        for start in range(0, len(request.texts), BATCH_SIZE):
            encoded = tokenizer(request.texts[start:start+BATCH_SIZE], padding=True,
                truncation=True, max_length=MAX_LENGTH, return_tensors='pt').to(DEVICE)
            vectors = model(**encoded).last_hidden_state[:, 0]
            vectors = F.normalize(vectors.float(), p=2, dim=1)
            output.extend(vectors.cpu().tolist())
    return {'vectors':output, 'model':'BAAI/bge-m3'}

@app.post('/rerank')
def rerank(request: RerankRequest):
    tokenizer, model = models['reranker']
    scores = []
    with lock, torch.inference_mode():
        for start in range(0, len(request.passages), BATCH_SIZE):
            pairs = [[request.query, text] for text in request.passages[start:start+BATCH_SIZE]]
            encoded = tokenizer(pairs, padding=True, truncation=True,
                max_length=MAX_LENGTH, return_tensors='pt').to(DEVICE)
            scores.extend(torch.sigmoid(model(**encoded).logits.float().reshape(-1)).cpu().tolist())
    return {'scores':scores, 'model':'BAAI/bge-reranker-v2-m3'}
