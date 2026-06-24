"use strict";exports.id=880,exports.ids=[880],exports.modules={70880:(a,b,c)=>{c.r(b);var d=c(87550),e=c.n(d),f=c(33873),g=c.n(f),h=c(29021),i=c.n(h);let j=process.env.DATA_DIR||process.cwd(),k=g().join(j,"./data/db.sqlite"),l=new(e())(k),m=g().join(j,"drizzle");l.exec(`
  CREATE TABLE IF NOT EXISTS ran_migrations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    run_on DATETIME DEFAULT CURRENT_TIMESTAMP
  );
`),i().readdirSync(m).filter(a=>a.endsWith(".sql")).sort().forEach(a=>{let b=g().join(m,a),c=i().readFileSync(b,"utf-8").split(/--> statement-breakpoint/g).map(a=>a.split(/\r?\n/).filter(a=>!a.trim().startsWith("--\x3e")).join("\n").trim()).filter(a=>a.length>0),d=a.split("_")[0]||a;if(l.prepare("SELECT 1 FROM ran_migrations WHERE name = ?").get(d))return void console.log(`Skipping already-applied migration: ${a}`);try{if("0001"===d){let a=l.prepare("SELECT id, type, metadata, content, chatId, messageId FROM messages").all();l.exec(`
                    CREATE TABLE IF NOT EXISTS messages_with_sources (
                        id INTEGER PRIMARY KEY,
                        type TEXT NOT NULL,
                        chatId TEXT NOT NULL,
                        createdAt TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        messageId TEXT NOT NULL,
                        content TEXT,
                        sources TEXT DEFAULT '[]'
                    );
                `);let b=l.prepare(`
                    INSERT INTO messages_with_sources (type, chatId, createdAt, messageId, content, sources)
                    VALUES (?, ?, ?, ?, ?, ?)
                `);a.forEach(a=>{for(;"string"==typeof a.metadata;)a.metadata=JSON.parse(a.metadata||"{}");if("user"===a.type)b.run("user",a.chatId,a.metadata.createdAt,a.messageId,a.content,"[]");else if("assistant"===a.type){b.run("assistant",a.chatId,a.metadata.createdAt,a.messageId,a.content,"[]");let c=a.metadata.sources||"[]";c&&c.length>0&&b.run("source",a.chatId,a.metadata.createdAt,`${a.messageId}-source`,"",JSON.stringify(c))}}),l.exec("DROP TABLE messages;"),l.exec("ALTER TABLE messages_with_sources RENAME TO messages;")}else if("0002"===d){l.exec(`
          CREATE TABLE IF NOT EXISTS chats_new (
            id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            createdAt TEXT NOT NULL,
            sources TEXT DEFAULT '[]',
            files TEXT DEFAULT '[]'
          );
        `);let a=l.prepare("SELECT id, title, createdAt, files FROM chats").all(),b=l.prepare(`
            INSERT INTO chats_new (id, title, createdAt, sources, files)
            VALUES (?, ?, ?, ?, ?)
          `);a.forEach(a=>{let c=a.files;for(;"string"==typeof c;)c=JSON.parse(c||"[]");b.run(a.id,a.title,a.createdAt,'["web"]',JSON.stringify(c))}),l.exec("DROP TABLE chats;"),l.exec("ALTER TABLE chats_new RENAME TO chats;"),l.exec(`
          CREATE TABLE IF NOT EXISTS messages_new (
            id INTEGER PRIMARY KEY,
            messageId TEXT NOT NULL,
            chatId TEXT NOT NULL,
            backendId TEXT NOT NULL,
            query TEXT NOT NULL,
            createdAt TEXT NOT NULL,
            responseBlocks TEXT DEFAULT '[]',
            status TEXT DEFAULT 'answering'
          );
        `);let c=l.prepare("SELECT id, messageId, chatId, type, content, createdAt, sources FROM messages ORDER BY id ASC").all(),d=l.prepare(`
            INSERT INTO messages_new (messageId, chatId, backendId, query, createdAt, responseBlocks, status)
            VALUES (?, ?, ?, ?, ?, ?, ?)
          `),e={},f=!0;c.forEach(a=>{if("user"===a.type&&f)(e={}).messageId=a.messageId,e.chatId=a.chatId,e.query=a.content,e.createdAt=a.createdAt,f=!1;else if("source"!==a.type||f)"assistant"!==a.type||f?"user"!==a.type||f||(d.run(e.messageId,e.chatId,`${e.messageId}-backend`,e.query,e.createdAt,JSON.stringify([{id:crypto.randomUUID(),type:"text",data:""},...e.sources&&e.sources.length>0?[{id:crypto.randomUUID(),type:"source",data:e.sources}]:[]]),"completed"),f=!0):(e.response=a.content,d.run(e.messageId,e.chatId,`${e.messageId}-backend`,e.query,e.createdAt,JSON.stringify([{id:crypto.randomUUID(),type:"text",data:e.response||""},...e.sources&&e.sources.length>0?[{id:crypto.randomUUID(),type:"source",data:e.sources}]:[]]),"completed"),f=!0);else{let b=a.sources;for(;"string"==typeof b;)b=JSON.parse(b||"[]");e.sources=b}}),l.exec("DROP TABLE messages;"),l.exec("ALTER TABLE messages_new RENAME TO messages;")}else c.forEach(a=>{a.trim()&&l.exec(a)});l.prepare("INSERT OR IGNORE INTO ran_migrations (name) VALUES (?)").run(d),console.log(`Applied migration: ${a}`)}catch(b){throw console.error(`Failed to apply migration ${a}:`,b),b}})}};