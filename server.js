const http=require('node:http');
const fs=require('node:fs');
const path=require('node:path');
require('./data.js');
const plan=require('./planner.js');
const assets = ['index.html', 'style.css', 'journeys.css', 'planner.js', 'data.js',
  'map.js', 'journeys.js', 'interface.js', 'hero-art.jpg', 'delhi-avenue.webp', 'mark.svg',
  'fonts/dm-sans.ttf', 'fonts/dm-serif-italic.ttf',
  'fonts/dm-sans-license.txt', 'fonts/dm-serif-license.txt'];
const files = Object.fromEntries(assets.map(name => ['/'+name, name]));
files['/'] = 'index.html';
const types = {'.html':'text/html; charset=utf-8','.css':'text/css','.js':'text/javascript',
  '.webp':'image/webp','.jpg':'image/jpeg','.svg':'image/svg+xml','.ttf':'font/ttf','.txt':'text/plain'};
http.createServer((req,res)=>{
  const url=new URL(req.url,'http://localhost');
  if(url.pathname==='/api/journeys'){
    res.setHeader('Content-Type','application/json');
    try{res.end(JSON.stringify(plan(...['from','to','date','time'].map(k=>url.searchParams.get(k)))));}
    catch(e){res.statusCode=400;res.end(JSON.stringify({error:e.message}));}
  }else if(Object.hasOwn(files,url.pathname)){
    res.setHeader('Content-Type',types[path.extname(files[url.pathname])]);
    res.end(fs.readFileSync(path.join(__dirname,files[url.pathname])));
  }else{res.writeHead(404);res.end();}
}).listen(Number(process.env.PORT || 8787),'127.0.0.1');
