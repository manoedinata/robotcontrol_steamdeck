const { createSocket } = require('dgram');

const server = createSocket('udp4');

server.on('message', (msg, rinfo) => {
    console.log(`Received message: "${msg}" from ${rinfo.address}:${rinfo.port}`);

});

server.on('error', (err) => {
    console.error(`Server error:\n${err.stack}`);
    server.close();
});

server.on('listening', () => {
    const address = server.address();
    console.log(`UDP Server listening on ${address.address}:${address.port}`);
});

const PORT = 41234;
const HOST = '127.0.0.1';
server.bind(PORT, HOST);
