# GraphSniper

GraphSniper is a small recon tool that extracts GraphQL queries and mutations directly from frontend JavaScript files. It's designed for situations where GraphQL introspection is disabled but the frontend bundles still expose the real queries the application uses.

The tool collects JavaScript files using:
- `katana`
- `gau`
- `waybackurls`

It downloads them in parallel, cleans them up, detects GraphQL endpoints, and extracts:
- Named queries
- Named mutations
- Variables used in each operation

Everything is saved to a clean JSON file.

---

## Install

### Python dependencies
```
pip install -r requirements.txt
```

### Install required Go tools
These are needed for collecting JS URLs:

```
go install github.com/projectdiscovery/katana/cmd/katana@latest
go install github.com/lc/gau/v2/cmd/gau@latest
go install github.com/tomnomnom/waybackurls@latest
```

---

## Usage
```
python3 GraphSniper.py example.com
```

Output will be written to:
```
graphql_output/<domain>_graphql_schema.json
```

JS files saved here:
```
js_files/
```

---

## What it does
- Finds the most likely GraphQL endpoint used by the frontend  
- Extracts all named queries & mutations  
- Pulls out variable definitions  
- Reconstructs readable, formatted GraphQL operations  
- Works passively (no API calls, no introspection)

---

## Example Output
```
{
  "endpoint": "https://api.example.com/graphql",
  "queries": {
    "GetUser": {
      "query": "query GetUser($id: ID!) { user(id: $id) { id username } }",
      "variables": ["id"]
    }
  }
}
```

---

## Notes
- Supports most modern frontends (Next.js, Relay, Apollo)  
- Good for bug bounty, pentesting, and API recon  
- No traffic hits the GraphQL API — everything is taken from JS files  
