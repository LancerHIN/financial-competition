"""核实助手：在指定题目的 gold 文档内，按关键词检索短原文片段。
用法： python script/_verify_helper.py <qid> "关键词1" "关键词2" ...
不传关键词时，打印题干+选项+doc_ids，便于先看题。
只输出命中点前后小窗口，严格控制输出长度，避免撑爆上下文。
"""
import json, glob, sys, io, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from agent.bm25_index import normalize_key  # 与 pipeline 同一归一化，去 strict_ 前缀

QMAP = {}
for f in glob.glob('questions/group_a/*.json'):
    for it in json.load(open(f, encoding='utf-8')):
        QMAP[it.get('qid')] = it

def load_docs(doc_ids):
    # 用 normalize_key 把题目 doc_id（如 csrc_0009_att1）映射到索引实际 doc_id
    # （如 strict_csrc_0009_att1），复现 pipeline 的 resolve_doc_ids 行为。
    want_keys = {normalize_key(str(d)): str(d) for d in doc_ids}
    by = {str(d): [] for d in doc_ids}
    for line in open('processed_data/parsed_pages.jsonl', encoding='utf-8'):
        line = line.strip()
        if not line:
            continue
        r = json.loads(line)
        did = str(r.get('doc_id'))
        k = normalize_key(did)
        if k in want_keys:
            by[want_keys[k]].append(r)
    return by

def main():
    qid = sys.argv[1]
    terms = sys.argv[2:]
    it = QMAP[qid]
    doc_ids = it.get('doc_ids') or []
    out = io.open('logs/_vh_out.txt', 'w', encoding='utf-8')
    out.write('QID=%s doc_ids=%s type=%s\n' % (qid, doc_ids, it.get('answer_format')))
    out.write('Q: ' + it['question'] + '\n')
    for L, v in it['options'].items():
        out.write('  %s: %s\n' % (L, v))
    out.write('\n')
    if terms:
        by = load_docs(doc_ids)
        for did in by:
            out.write('===== doc %s (chunks=%d) =====\n' % (did, len(by[did])))
            for term in terms:
                hits = 0
                for r in by[did]:
                    t = r.get('text', '')
                    idx = t.find(term)
                    if idx >= 0:
                        seg = t[max(0, idx-70):idx+110].replace('\n', ' ')
                        out.write('[%s|p%s] ...%s...\n' % (term, r.get('page'), seg))
                        hits += 1
                        if hits >= 4:
                            break
                if hits == 0:
                    out.write('[%s] 未命中\n' % term)
            out.write('\n')
    out.close()
    print('written logs/_vh_out.txt')

if __name__ == '__main__':
    main()
