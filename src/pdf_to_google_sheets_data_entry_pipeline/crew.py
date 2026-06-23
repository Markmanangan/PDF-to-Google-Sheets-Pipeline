import os

from crewai import LLM
from crewai import Agent, Crew, Process, Task
from crewai.project import CrewBase, agent, before_kickoff, crew, task

from pdf_to_google_sheets_data_entry_pipeline.tools.pdf_table_extractor_tool import PDFTableExtractorTool


@CrewBase
class PdfToGoogleSheetsDataEntryPipelineCrew:
    """PdfToGoogleSheetsDataEntryPipeline crew"""

    @before_kickoff
    def preload_pdf_extraction(self, inputs):
        pdf_path = inputs.get("pdf_file_path")
        if pdf_path:
            inputs["pdf_extraction_json"] = PDFTableExtractorTool()._run(pdf_path)
        return inputs

    
    @agent
    def pdf_reader_agent(self) -> Agent:

        return Agent(
            config=self.agents_config["pdf_reader_agent"],
            
            
            tools=[],
            reasoning=False,
            max_reasoning_attempts=None,
            inject_date=True,
            allow_delegation=False,
            max_iter=25,
            max_rpm=None,
            
            
            max_execution_time=None,
            llm=LLM(
                model="groq/llama-3.3-70b-versatile",
                
                
            ),
            
        )
        
    
    @agent
    def data_extraction_agent(self) -> Agent:
        
        
        return Agent(
            config=self.agents_config["data_extraction_agent"],
            
            
            tools=[],
            reasoning=False,
            max_reasoning_attempts=None,
            inject_date=True,
            allow_delegation=False,
            max_iter=25,
            max_rpm=None,
            
            
            max_execution_time=None,
            llm=LLM(
                model="groq/llama-3.3-70b-versatile",
                
                
            ),
            
        )
        
    
    @agent
    def data_validation_agent(self) -> Agent:
        
        
        return Agent(
            config=self.agents_config["data_validation_agent"],
            
            
            tools=[],
            reasoning=False,
            max_reasoning_attempts=None,
            inject_date=True,
            allow_delegation=False,
            max_iter=25,
            max_rpm=None,
            
            
            max_execution_time=None,
            llm=LLM(
                model="groq/llama-3.3-70b-versatile",
                
                
            ),
            
        )
        
    
    @agent
    def google_sheets_writer_agent(self) -> Agent:
        
        
        return Agent(
            config=self.agents_config["google_sheets_writer_agent"],
            
            
            tools=[],
            reasoning=False,
            max_reasoning_attempts=None,
            inject_date=True,
            allow_delegation=False,
            max_iter=25,
            max_rpm=None,
            
            apps=[
                    "google_sheets/get_values",
                    
                    "google_sheets/append_values",
                    ],
            
            
            max_execution_time=None,
            llm=LLM(
                model="groq/llama-3.3-70b-versatile",
                
                
            ),
            
        )
        
    
    @agent
    def reporting_agent(self) -> Agent:
        
        
        return Agent(
            config=self.agents_config["reporting_agent"],
     
            tools=[],
            reasoning=False,
            max_reasoning_attempts=None,
            inject_date=True,
            allow_delegation=False,
            max_iter=25,
            max_rpm=None,
            
            
            max_execution_time=None,
            llm=LLM(
                model="groq/llama-3.3-70b-versatile",
                
                
            ),
            
        )
        
    @task
    def read_pdf_table(self) -> Task:
        return Task(
            config=self.tasks_config["read_pdf_table"],
            markdown=False,
            
            
        )
    
    @task
    def extract_structured_data(self) -> Task:
        return Task(
            config=self.tasks_config["extract_structured_data"],
            markdown=False,
            
            
        )
    
    @task
    def validate_extracted_records(self) -> Task:
        return Task(
            config=self.tasks_config["validate_extracted_records"],
            markdown=False,
            
            
        )
    
    @task
    def write_data_to_google_sheets(self) -> Task:
        return Task(
            config=self.tasks_config["write_data_to_google_sheets"],
            markdown=False,
            
            
        )
    
    @task
    def generate_processing_report(self) -> Task:
        return Task(
            config=self.tasks_config["generate_processing_report"],
            markdown=False,
            
            
        )
    

    @crew
    def crew(self) -> Crew:
        """Creates the PdfToGoogleSheetsDataEntryPipeline crew"""

        return Crew(
            agents=self.agents,  # Automatically created by the @agent decorator
            tasks=self.tasks,  # Automatically created by the @task decorator
            process=Process.sequential,
            verbose=True,

            chat_llm=LLM(model="groq/llama-3.3-70b-versatile"),
        )


