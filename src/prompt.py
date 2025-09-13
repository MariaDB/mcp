"""
Prompt template collection for MariaDB MCP Server

This module provides various prompt templates for database analysis and optimization.
"""

def get_explain_table_prompt(table_name: str) -> str:
    """
    Returns a prompt template for table structure analysis.
    
    Args:
        table_name: Name of the table to analyze
        
    Returns:
        Formatted prompt string
    """
    return f"""Using DB MCP, analyze the schema and relationships of the {table_name} table in the database,
and create documentation in an easy-to-read Markdown format.
Before proceeding with this task, call List_databases to confirm which database to work with.
If backend code exists in the user's codebase, please reference it to explain the role of this table.

### Output Format (Markdown) (Write in the following format)

# 📋 Table Analysis: {table_name}

## 1. Table Overview
- **Name**: {table_name}
- **Type**: General
- **Role**: Store billing details and manage payment status

## 2. Column Analysis
| Column Name     | Type         | PK/FK | NULL Allowed | Default | Description |
|----------------|--------------|-------|--------------|---------|-------------|
| id             | BIGINT       | PK    | NO           | -       | Unique identifier |
| member_id      | BIGINT       | FK    | NO           | -       | Member ID (member.id) |
| amount         | DECIMAL(10,2)|       | NO           | 0.00    | Billing amount |
| status         | TINYINT      |       | NO           | 0       | Payment status (0:pending, 1:completed, 2:cancelled) |
...

## 3. Relationship Analysis
### Parent Tables
- member (member_id) → ON DELETE CASCADE / ON UPDATE CASCADE, billing target member information

### Child Tables
- payment (charge_id) → payment history reference

## 4. Key Indexes
- PK Index: PRIMARY KEY (id)
- Secondary Index: idx_member_id (member_id) → improve performance for member-based queries"""

def get_query_tuning_prompt(original_query: str) -> str:
    """
    Returns a prompt template for query performance analysis.
    
    Args:
        original_query: Original query to analyze
        database_name: Database name
        optimization_focus: Optimization focus area
        
    Returns:
        Formatted prompt string
    """
    return f"""You are a MariaDB/MySQL query performance optimization expert (DBA).  
Using MariaDB MCP, provide **execution plan analysis, bottleneck causes, and specific improvement suggestions** for the query below.
Before proceeding with this task, call List_databases to confirm which database to work with.

---

### Target Query for Analysis
```sql
{original_query}
```

### 1. Execution Plan Analysis
- Interpret each column (type, rows, key, Extra) based on EXPLAIN
- Specifically identify bottleneck sections and causes (full scan, filesort, temporary tables, etc.)
- Explain potential impacts in large table environments

### 2. Optimization Suggestions
- Remove unnecessary operations/conditions
- Suggest appropriate index creation SQL
- Improve JOIN, WHERE, GROUP BY, ORDER BY structure
- Assess feasibility of converting subqueries to JOINs
- Propose LIMIT or data range limitation methods
- If there are specific columns that absolutely need index settings, you may suggest them
- Before making suggestions, make sure to execute the improved query to confirm there are no issues

### 3. Improved Query Example
- Maintain same results as original
- Present improved query that can expect performance enhancement

```sql
[Improved Query]
```

### 4. Expected Performance Impact
- Expected execution time change
- Change in number of rows examined
- Change in index usage

*Output format must follow the above structure (Analysis → Suggestions → Improved Query → Impact),
use concise technical terms and explanations that both DBAs and developers can understand.*
"""
