"""Make the published (claude.ai Artifact) page from city/index.html.

    python3 city/build_page.py city/index.html out/index.html

The host wraps the page in its own doctype/head/body, so those are stripped,
and it doesn't serve .glb files, so the page loads each model from
assets/<name>.glb.txt (the .glb as base64). The page may not fetch the Draco
decoder from a CDN either, so publish models without Draco compression:

    npx @gltf-transform/cli copy city/assets/city.glb out/assets/city.glb
    base64 -w0 out/assets/city.glb > out/assets/city.glb.txt
(the same for actors.glb, cars.glb and player.glb, and for city/assets/npc/<id>.glb as
assets/npc/<id>.glb.txt), plus city/assets/tex/*.jpg. The city's people load after the
city, outside the loading bar, and their list (city/assets/npc/npcs.json) is put
into the page.
"""
import sys, re, os, json
src, out = sys.argv[1], sys.argv[2]
s = open(src, encoding='utf-8').read()
for pat in (r'<!doctype html>\s*', r'<html[^>]*>\s*', r'<head>\s*', r'<meta charset="utf-8">\s*', r'<meta name="viewport"[^>]*>\s*',
            r'</head>\s*', r'<body>\s*', r'</body>\s*', r'</html>\s*'):
    s, n = re.subn(pat, '', s, count=1, flags=re.I)
    assert n == 1, pat
# the page is Hebrew, right to left
s = s.replace('<title>Sunset Bay</title>', '<title>Sunset Bay</title>\n<script>document.documentElement.lang="he";document.documentElement.dir="rtl";</script>', 1)
assert '<title>Sunset Bay</title>' in s[:400]
open(out, 'w', encoding='utf-8').write(s)
print('ok', len(s))

# the page's host doesn't serve .glb files: the models go as base64 text beside
# the page (assets/X.glb.txt), decoded here into the same glTF loader
anchor = "gltfL.setDRACOLoader(draco);"
assert s.count(anchor) == 1
loader = anchor + """
// (published page) the models come as base64 text: decode them and hand them to the glTF loader
function loadB64(url,onLoad,onProgress,onError){
  manager.itemStart(url);
  const bad=e=>{(onError||console.error)(e);manager.itemError(url);manager.itemEnd(url);};
  fetch(url+'.txt').then(r=>{if(!r.ok)throw new Error('HTTP '+r.status+' '+url);return r.text();}).then(t=>{
    const bin=atob(t.trim()),b=new Uint8Array(bin.length);for(let i=0;i<bin.length;i++)b[i]=bin.charCodeAt(i);
    gltfL.parse(b.buffer,'assets/',g=>{onLoad(g);manager.itemEnd(url);},bad);
  }).catch(bad);
}"""
s = s.replace(anchor, loader, 1)
for m in ('city', 'actors', 'cars', 'player'):
    a = "gltfL.load('assets/%s.glb'," % m
    assert s.count(a) == 1, a
    s = s.replace(a, "loadB64('assets/%s.glb'," % m)
# the people: base64 too, but not through the loading manager (they come after the city is built)
anchor = "const npcL=new THREE.GLTFLoader();"
assert s.count(anchor) == 1
s = s.replace(anchor, anchor + """
function loadB64npc(url,onLoad,onProgress,onError){
  const bad=onError||console.error;
  fetch(url+'.txt').then(r=>{if(!r.ok)throw new Error('HTTP '+r.status+' '+url);return r.text();}).then(t=>{
    const bin=atob(t.trim()),b=new Uint8Array(bin.length);for(let i=0;i<bin.length;i++)b[i]=bin.charCodeAt(i);
    npcL.parse(b.buffer,'assets/npc/',onLoad,bad);
  }).catch(bad);
}""", 1)
a = "npcL.load('assets/npc/'+id+'.glb',"
assert s.count(a) == 1, a
s = s.replace(a, "loadB64npc('assets/npc/'+id+'.glb',")
a = "new THREE.FileLoader().load('assets/npc/npcs.json',t=>{try{loadNPCs(JSON.parse(t));}catch(e){console.warn('people',e);}},undefined,e=>console.warn('people',e));"
assert s.count(a) == 1, a
npcs = json.load(open(os.path.join(os.path.dirname(os.path.abspath(src)), 'assets', 'npc', 'npcs.json')))
s = s.replace(a, "loadNPCs(%s);" % json.dumps(npcs, separators=(',', ':')))
open(out, 'w', encoding='utf-8').write(s)
print('base64 loaders in;', len(npcs), 'people')
