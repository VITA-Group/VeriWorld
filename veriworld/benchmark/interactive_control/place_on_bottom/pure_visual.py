"""PlaceOnBottom pure-visual condition (release).

Runs the unchanged VISION harness, but rewrites each outbound round so the model
receives only the screenshot + the allowed anonymous target-ID vocabulary: the
"Anonymous state (positions + scale_cm only)" block and all geometric feedback
are removed, the image and the round / own-last-action echo are kept verbatim.
Derived byte-for-byte from the 2026-11-05 experiment transform; verified by an
offline test that reproduces the captured sent-message hashes.
"""
import ast,copy,hashlib,json,math,re,threading
from pathlib import Path

CONDITION='pure_visual'
TASK=Path(__file__).resolve().parent/'harness_vision'/'task.py'
SYSTEM_PROMPT="""You are an agent in a 3D physics scene. There is one blue physics-enabled CUBE, one yellow PLATFORM, and distractor objects. Your goal is to put the blue cube on top of the yellow platform so that it settles there.

Each round you receive a SCREENSHOT and an Allowed action target IDs list. The IDs are action vocabulary only; no ID is identified as the cube, platform, or a distractor. Use the images and the effects of your actions to determine which object to manipulate. No object positions, sizes, role mapping, camera pose, or target coordinates are supplied.

ACTION FORMAT — emit ONE JSON code fence per round:
```json
{"cmd": "probe", "label": "PLACE_OBJ_2"}
{"cmd": "move_to", "label": "PLACE_OBJ_2", "to": [x, y, z]}
{"cmd": "stop"}
```

Use an ID from Allowed action target IDs. The example ID is only syntax, not a target hint. probe lifts that object by 200 cm so the next screenshot reveals which object it is; the harness restores it afterward. move_to teleports that object to absolute world X, Y, Z coordinates in cm and zeros its velocity. Z is vertical. stop ends the episode and lets physics settle before verification.

Probe sparingly (1–3 times). After moving the cube on top of the platform, emit stop. PASS requires the cube to settle on the platform with its footprint inside the platform footprint and its bottom at platform-top height within 5 cm. The original task verifier is unchanged.

You have 8 rounds.
"""

def encode(value):return json.dumps(value,ensure_ascii=False,separators=(',',':'),allow_nan=False).encode('utf-8')
def image_count(messages):return sum(p.get('type')=='image_url'for m in messages if isinstance(m.get('content'),list)for p in m['content'])
def source_system():
 tree=ast.parse(TASK.read_text(encoding='utf-8'))
 return next(ast.literal_eval(n.value)for n in tree.body if isinstance(n,ast.Assign)and any(isinstance(t,ast.Name)and t.id=='SYSTEM_PROMPT'for t in n.targets)).format(max_rounds=8)
def original_parser(text):
 # Identical action extraction function imported from the unchanged scientific source.
 from veriworld.benchmark.interactive_control.place_on_bottom._shared import extract_action
 return extract_action(text)
def own_action_summary(action):
 """Reproduce only the source's echo of the model-submitted command, no native state."""
 if not action or 'cmd'not in action:return None
 cmd=action['cmd']
 if cmd=='probe':
  label=str(action.get('label','')).strip();return f"probed {label if label else None}"
 if cmd=='move_to':
  label=str(action.get('label','')).strip();to=action.get('to')or action.get('position')
  if not label or not(isinstance(to,list)and len(to)>=3):return 'moved None to []'
  values=[round(float(to[i]),1)for i in range(3)]
  return f'moved {label} to {values}'
 if cmd=='stop':return 'stopped'
 return str(cmd)
def expected_tail(round_index,prior_action):
 assert type(round_index)is int and 1<=round_index<=8
 tail=f'**Round {round_index}/8** ({8-round_index} remaining)\n\nEmit one JSON action (probe / move_to / stop). After moving the cube onto the platform, stop so physics can settle.'
 last=own_action_summary(prior_action)
 if last:tail+='\n\nLast action: '+last
 return tail
def transform(messages,round_index,prior_action=None):
 assert isinstance(messages,list)and len(messages)==2 and all(set(m)=={'role','content'}for m in messages)
 assert messages[0]=={'role':'system','content':source_system()}and messages[1]['role']=='user'
 parts=messages[1]['content'];assert isinstance(parts,list)and len(parts)==3
 image,statepart,tail=parts
 assert set(image)=={'type','image_url'}and image['type']=='image_url'and set(image['image_url'])=={'url'}
 assert image['image_url']['url'].startswith('data:image/png;base64,')
 assert set(statepart)=={'type','text'}and statepart['type']=='text'
 prefix='Anonymous state (positions + scale_cm only):\n```json\n';s=statepart['text'];assert s.startswith(prefix)and s.endswith('\n```')
 state=json.loads(s[len(prefix):-4]);assert isinstance(state,list)and 4<=len(state)<=10
 assert all(set(row)=={'label','position','scale_cm'}for row in state)
 labels=[row['label']for row in state];assert len(labels)==len(set(labels))
 assert all(isinstance(label,str)and re.fullmatch(r'PLACE_OBJ_[0-9]',label)for label in labels)
 assert tail=={'type':'text','text':expected_tail(round_index,prior_action)},'Feedback must be original echo of prior model command, never native coordinates'
 vocabulary={'type':'text','text':'Allowed action target IDs:\n'+ '\n'.join(labels)}
 out=[{'role':'system','content':SYSTEM_PROMPT},{'role':'user','content':[copy.deepcopy(image),vocabulary,copy.deepcopy(tail)]}]
 assert image_count(out)==1 and out[1]['content'][0]==image and out[1]['content'][2]==tail
 proof=dict(condition=CONDITION,source_condition='V',round_index=round_index,prior_model_action=prior_action,
  source_messages_sha256=hashlib.sha256(encode(messages)).hexdigest(),sent_messages_sha256=hashlib.sha256(encode(out)).hexdigest(),
  source_state_sha256=hashlib.sha256(encode(state)).hexdigest(),allowed_target_ids=labels,original_image_count=1,sent_image_count=1,
  image_part_unchanged=True,instance_geometry_removed=True,label_order_unchanged=True,source_action_echo_unchanged=True,
  model_visible_text_provenance=['fixed_pure_visual_system','original_state_label_only','original_round_and_prior_model_command_echo'])
 return out,proof


class PureVisualOutbound:
    """Stateful per-episode wrapper: apply the pure-visual transform to each
    round's V messages and track the model's own last action for the echo.
    No native geometry is ever forwarded."""

    def __init__(self):
        self.round_index = 0
        self.prior_action = None

    def apply(self, messages):
        self.round_index += 1
        sent, proof = transform(messages, self.round_index, self.prior_action)
        return sent, proof

    def observe_reply(self, response_text):
        self.prior_action = original_parser(response_text)


__all__ = ["CONDITION", "SYSTEM_PROMPT", "transform", "original_parser",
           "expected_tail", "own_action_summary", "PureVisualOutbound"]
