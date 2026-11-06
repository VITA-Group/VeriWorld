"""Save response identity and input hashes without authorization headers."""
import base64
import hashlib
import json
from pathlib import Path


def save_api_receipt(vlm, messages, workspace, round_idx, *, error=None):
    workspace=Path(workspace)
    images=[]
    for message in messages:
        content=message.get('content')
        if not isinstance(content,list):continue
        for part in content:
            if part.get('type')=='image_url':
                url=part['image_url']['url']
                if url.startswith('data:'):
                    images.append(hashlib.sha256(base64.b64decode(url.split(',',1)[1])).hexdigest())
    receipt={'requested_model':getattr(vlm,'model',None),
             'returned_model':getattr(vlm,'last_returned_model',None),
             'finish_reason':getattr(vlm,'last_raw_finish_reason',None),
             'messages_sha256':hashlib.sha256(json.dumps(messages,sort_keys=True).encode()).hexdigest(),
             'image_sha256':images,'round':round_idx,
             'api_call_completed':error is None,'error_type':type(error).__name__ if error is not None else None,
             'raw_response':getattr(vlm,'last_response',None)}
    path=workspace/f'round_{round_idx:03d}_api.json'
    path.write_text(json.dumps(receipt,indent=2,ensure_ascii=False),encoding='utf-8')
    if error is not None:
        (workspace/'unscored_error.json').write_text(json.dumps({
            'valid_scored_episode':False,'round':round_idx,'error_type':type(error).__name__,
            'api_receipt':path.name},indent=2),encoding='utf-8')
