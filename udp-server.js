const { createSocket } = require('dgram');

// 1. Create a UDP socket for IPv4
const server = createSocket('udp4');

// 2. Handle incoming messages
server.on('message', (msg, rinfo) => {
    console.log(`Received message: "${msg}" from ${rinfo.address}:${rinfo.port}`);

    // Optional: Send a response back to the client
    const response = Buffer.from('Message received safely!');
    server.send(response, rinfo.port, rinfo.address, (err) => {
        if (err) {
            console.error('Failed to send response:', err);
        }
    });
});

// 3. Handle errors
server.on('error', (err) => {
    console.error(`Server error:\n${err.stack}`);
    server.close();
});

// 4. Handle binding confirmation
server.on('listening', () => {
    const address = server.address();
    console.log(`UDP Server listening on ${address.address}:${address.port}`);
});

// 5. Bind to a port and address
const PORT = 41234;
const HOST = '127.0.0.1'; // Listen locally
server.bind(PORT, HOST);
