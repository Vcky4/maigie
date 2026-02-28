import os
import re

file_path = "src/services/llm_service.py"
with open(file_path, "r", encoding="utf-8") as f:
    content = f.read()

# 1. Imports
content = re.sub(
    r"import google\.generativeai as _genai",
    "from google import genai as _genai\n        from google.genai import types as _types",
    content,
)
content = content.replace(
    "from google.generativeai.types import HarmBlockThreshold, HarmCategory",
    "HarmBlockThreshold = _types.HarmBlockThreshold\n    HarmCategory = _types.HarmCategory",
)
content = content.replace("import google.generativeai as genai", "from google import genai")

# 2. GeminiService init
content = content.replace(
    """        self.model = genai.GenerativeModel(
            model_name="models/gemini-3-flash-preview", system_instruction=SYSTEM_INSTRUCTION
        )""",
    """        self.model_name = "gemini-3-flash-preview"
        self.system_instruction = SYSTEM_INSTRUCTION
        self.client = _genai.Client(api_key=os.getenv("GEMINI_API_KEY"))""",
)

# 3. get_chat_response start_chat
content = content.replace(
    """            # Start a chat session with history
            chat = self.model.start_chat(history=history)

            # Send the enhanced message
            response = await chat.send_message_async(
                enhanced_message, safety_settings=self.safety_settings
            )""",
    """            # Start a chat session with history
            chat = self.client.aio.chats.create(
                model=self.model_name,
                history=history,
                config=_types.GenerateContentConfig(
                    system_instruction=self.system_instruction,
                    safety_settings=self.safety_settings,
                )
            )

            # Send the enhanced message
            response = await chat.send_message(enhanced_message)""",
)

# 4. get_chat_response_with_tools
content = content.replace(
    """            # Create model with tools
            model_with_tools = genai.GenerativeModel(
                model_name="models/gemini-3-flash-preview",
                system_instruction=system_instruction,
                tools=tools,
            )""",
    """            # Create client
            client = _genai.Client(api_key=os.getenv("GEMINI_API_KEY"))""",
)

content = content.replace(
    """            # Start chat session
            chat = model_with_tools.start_chat(history=processed_history)""",
    """            # Start chat session
            chat = client.aio.chats.create(
                model="gemini-3-flash-preview",
                history=processed_history,
                config=_types.GenerateContentConfig(
                    system_instruction=system_instruction,
                    tools=[tools] if tools else None,
                    safety_settings=self.safety_settings,
                )
            )""",
)

content = content.replace(
    """                    response_stream = await chat.send_message_async(
                        payload, safety_settings=self.safety_settings, stream=True
                    )""",
    """                    response_stream = await chat.send_message_stream(payload)""",
)
content = content.replace(
    """                        response = await chat.send_message_async(
                            message_content, safety_settings=self.safety_settings
                        )""",
    """                        response = await chat.send_message(message_content)""",
)
content = content.replace(
    """                        response = await chat.send_message_async(
                            tool_results, safety_settings=self.safety_settings
                        )""",
    """                        response = await chat.send_message(tool_results)""",
)

content = content.replace(
    "genai.protos.FunctionResponse(name=tool_name, response=tool_result)",
    "_types.Part.from_function_response(name=tool_name, response=tool_result)",
)

# 5. Extract user facts
content = content.replace(
    """            model = genai.GenerativeModel(model_name="models/gemini-2.0-flash-lite")
            response = await asyncio.to_thread(model.generate_content, extraction_prompt)""",
    """            client = _genai.Client(api_key=os.getenv("GEMINI_API_KEY"))
            response = await client.aio.models.generate_content(
                model="gemini-2.0-flash-lite",
                contents=extraction_prompt
            )""",
)

# 6. generate_summary
content = content.replace(
    """            response = await self.model.generate_content_async(
                summary_prompt, safety_settings=self.safety_settings
            )""",
    """            response = await self.client.aio.models.generate_content(
                model=self.model_name,
                contents=summary_prompt,
                config=_types.GenerateContentConfig(safety_settings=self.safety_settings)
            )""",
)

# 7. generate_minimal_response
content = content.replace(
    """            minimal_model = genai.GenerativeModel(
                "gemini-2.0-flash-lite",
                generation_config=genai.types.GenerationConfig(
                    max_output_tokens=max_tokens,
                    temperature=0.7,
                ),
                safety_settings=self.safety_settings,
            )

            response = await minimal_model.generate_content_async(
                prompt, safety_settings=self.safety_settings
            )""",
    """            client = _genai.Client(api_key=os.getenv("GEMINI_API_KEY"))
            response = await client.aio.models.generate_content(
                model="gemini-2.0-flash-lite",
                contents=prompt,
                config=_types.GenerateContentConfig(
                    max_output_tokens=max_tokens,
                    temperature=0.7,
                    safety_settings=self.safety_settings,
                )
            )""",
)

# 8. generate_course_outline
content = content.replace(
    """            outline_model = genai.GenerativeModel(
                "gemini-2.0-flash",
                system_instruction=SYSTEM_INSTRUCTION,
                generation_config=genai.types.GenerationConfig(
                    max_output_tokens=900,
                    temperature=0.2,
                ),
                safety_settings=self.safety_settings,
            )

            user_msg = user_message or ""
            prompt =""",
    """            user_msg = user_message or ""
            prompt =""",
)

content = content.replace(
    """            response = await outline_model.generate_content_async(
                prompt, safety_settings=self.safety_settings
            )""",
    """            client = _genai.Client(api_key=os.getenv("GEMINI_API_KEY"))
            response = await client.aio.models.generate_content(
                model="gemini-2.0-flash",
                contents=prompt,
                config=_types.GenerateContentConfig(
                    system_instruction=SYSTEM_INSTRUCTION,
                    max_output_tokens=900,
                    temperature=0.2,
                    safety_settings=self.safety_settings,
                )
            )""",
)

# 9. rewrite_note_content
content = content.replace(
    """            response = await self.model.generate_content_async(
                rewrite_prompt, safety_settings=self.safety_settings
            )""",
    """            response = await self.client.aio.models.generate_content(
                model=self.model_name,
                contents=rewrite_prompt,
                config=_types.GenerateContentConfig(safety_settings=self.safety_settings)
            )""",
)

# 10. generate_tags
content = content.replace(
    """            response = await self.model.generate_content_async(
                tag_prompt, safety_settings=self.safety_settings
            )""",
    """            response = await self.client.aio.models.generate_content(
                model=self.model_name,
                contents=tag_prompt,
                config=_types.GenerateContentConfig(safety_settings=self.safety_settings)
            )""",
)

# 11. analyze_image
content = content.replace(
    """            response = await self.model.generate_content_async(
                content, safety_settings=self.safety_settings
            )""",
    """            # Convert image bytes to part and prepare content list properly
            from google.genai import types as genai_types
            img_part = genai_types.Part.from_bytes(data=image_data, mime_type=mime_type)
            new_content = [prompt, img_part]
            response = await self.client.aio.models.generate_content(
                model=self.model_name,
                contents=new_content,
                config=_types.GenerateContentConfig(safety_settings=self.safety_settings)
            )""",
)

# 12. extract_exam_topics
content = content.replace(
    """        genai.configure(api_key=os.getenv("GEMINI_API_KEY"))
        model = genai.GenerativeModel(
            "gemini-2.0-flash",
            generation_config=genai.types.GenerationConfig(
                max_output_tokens=2000,
                temperature=0.2,
            ),
        )
        response = await model.generate_content_async(prompt)""",
    """        client = _genai.Client(api_key=os.getenv("GEMINI_API_KEY"))
        from google.genai import types as genai_types
        response = await client.aio.models.generate_content(
            model="gemini-2.0-flash",
            contents=prompt,
            config=genai_types.GenerateContentConfig(
                max_output_tokens=2000,
                temperature=0.2,
            )
        )""",
)

# 13. extract_past_paper_questions
content = content.replace(
    """        genai.configure(api_key=os.getenv("GEMINI_API_KEY"))
        model = genai.GenerativeModel(
            "gemini-2.0-flash",
            generation_config=genai.types.GenerationConfig(
                max_output_tokens=4000,
                temperature=0.1,
            ),
        )
        response = await model.generate_content_async(prompt)""",
    """        client = _genai.Client(api_key=os.getenv("GEMINI_API_KEY"))
        from google.genai import types as genai_types
        response = await client.aio.models.generate_content(
            model="gemini-2.0-flash",
            contents=prompt,
            config=genai_types.GenerateContentConfig(
                max_output_tokens=4000,
                temperature=0.1,
            )
        )""",
)

# 14. generate_exam_questions
content = content.replace(
    """        genai.configure(api_key=os.getenv("GEMINI_API_KEY"))
        model = genai.GenerativeModel(
            "gemini-2.0-flash",
            generation_config=genai.types.GenerationConfig(
                max_output_tokens=4000,
                temperature=0.4,
            ),
        )
        response = await model.generate_content_async(prompt)""",
    """        client = _genai.Client(api_key=os.getenv("GEMINI_API_KEY"))
        from google.genai import types as genai_types
        response = await client.aio.models.generate_content(
            model="gemini-2.0-flash",
            contents=prompt,
            config=genai_types.GenerateContentConfig(
                max_output_tokens=4000,
                temperature=0.4,
            )
        )""",
)

# 15. get_schedule_review_suggestions
content = content.replace(
    """        genai.configure(api_key=os.getenv("GEMINI_API_KEY"))
        model = genai.GenerativeModel(
            "models/gemini-2.0-flash",
            generation_config=genai.types.GenerationConfig(
                max_output_tokens=600,
                temperature=0.3,
            ),
        )
        response = await model.generate_content_async(prompt)""",
    """        client = _genai.Client(api_key=os.getenv("GEMINI_API_KEY"))
        from google.genai import types as genai_types
        response = await client.aio.models.generate_content(
            model="gemini-2.0-flash",
            contents=prompt,
            config=genai_types.GenerateContentConfig(
                max_output_tokens=600,
                temperature=0.3,
            )
        )""",
)

with open(file_path, "w", encoding="utf-8") as f:
    f.write(content)
print("Migration completed successfully!")
