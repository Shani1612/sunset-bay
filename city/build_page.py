"""Make the published (claude.ai Artifact) page from city/index.html.

    python3 city/build_page.py city/index.html out/index.html

The host wraps the page in its own doctype/head/body, so those are stripped,
and it doesn't serve .glb files, so the page loads each model from
assets/<name>.glb.txt (the .glb as base64). The page may not fetch the Draco
decoder from a CDN either, so publish models without Draco compression:

    npx @gltf-transform/cli copy city/assets/city.glb out/assets/city.glb
    base64 -w0 out/assets/city.glb > out/assets/city.glb.txt
(the same for actors.glb and player.glb), plus city/assets/tex/*.jpg.
"""
import sys, re
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
for m in ('city', 'actors', 'player'):
    a = "gltfL.load('assets/%s.glb'," % m
    assert s.count(a) == 1, a
    s = s.replace(a, "loadB64('assets/%s.glb'," % m)
open(out, 'w', encoding='utf-8').write(s)
print('base64 loader in')
