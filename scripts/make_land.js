// Extract Australia (ISO 036) from Natural Earth 50m (world-atlas) -> compact GeoJSON
const fs = require('fs'), path = require('path');
const topo = require(process.argv[2] + '/topojson-client-3.1.0/dist/topojson-client.js');
const world = JSON.parse(fs.readFileSync(process.argv[2] + '/world-atlas-2.0.2/countries-50m.json'));
const fc = topo.feature(world, world.objects.countries);
const au = fc.features.filter(f => f.id === '036');
const round = c => Array.isArray(c[0]) ? c.map(round) : [Math.round(c[0]*100)/100, Math.round(c[1]*100)/100];
au.forEach(f => f.geometry.coordinates = round(f.geometry.coordinates));
fs.writeFileSync(process.argv[3], JSON.stringify({type:'FeatureCollection', features: au.map(f=>({type:'Feature',properties:{name:'Australia'},geometry:f.geometry}))}));
console.log('ok', au.length, fs.statSync(process.argv[3]).size);
