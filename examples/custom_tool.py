"""An offline API example. Replace ScriptedModel with ChatModel for inference."""

from nreact import Agent, ScriptedModel, Tool, ToolEnvironment

stock = {"notebook": 12, "pencil": 40}
environment = ToolEnvironment([
    Tool("stock", "Return available units for a product name.", lambda name: str(stock.get(name, 0))),
])
agent = Agent(ScriptedModel([
    "Thought: Check the inventory.\nAction: Stock[notebook]",
    "Thought: Inventory returned 12.\nAction: Finish[12 notebooks are available.]",
]), environment)
result = agent.run("How many notebooks are available?")
print(result.answer)
