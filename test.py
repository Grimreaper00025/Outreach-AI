import asyncio
from mira_sdk import MiraClient, Flow, File, Reader

async def test_mira_api():
    # Replace this with your actual Mira API key.
    client = MiraClient(config={"API_KEY": "sb-f5d7f4055a7cabad7b8d5a848453c8f8"})
    
    # Load your flow YAML file.
    # The YAML file should be structured as per your Mira Flow specification.
    flow = Flow(source="/Users/vishalshahi/Desktop/OutreachAI/mira.yaml")
    
    # Build the input dictionary for the Mira flow.
    # For this example:
    # - input1 is provided via a File (e.g., "user.txt")
    # - input2 is provided via a URL Reader (e.g., a placeholder URL)
    # - input3 is a simple string (e.g., "John Doe")
    additional_context = (
        "Generate a professional academic cold outreach email that meets the following requirements:\n"
        "- Describe the user's relevant background based on the CV.\n"
        "- Explain why the user is interested in the professor's research (referring to the professor's interests and work highlight).\n"
        "- Include a clear call-to-action for potential collaboration.\n"
        "Modify the language as needed so the email is engaging and professional."
    )
    input_dict = {
        "input1": "ML , Ai , Deepseek and recently worked on doing on making deepseek cheeper",
        "input2": "ml , fl and more ",
        "input3": additional_context
    }
    
    # Call the Mira flow test method.
    response = client.flow.test(flow, input_dict)
    
    # Print the response (expected to be a JSON object containing your flow's output)
    print("Response from Mira API:")
    print(response)
    print(response.get("result", "No email generated."))

if __name__ == '__main__':
    asyncio.run(test_mira_api())
